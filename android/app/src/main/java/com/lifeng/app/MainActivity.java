package com.lifeng.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.View;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

/**
 * 砺锋安卓端。
 *
 * <p><b>它是什么</b>：一个指向老师电脑上砺锋服务的客户端。学生装一次，
 * 以后点图标就用，不必每次输网址。
 *
 * <p><b>它不是什么</b>：它没有把砺锋装进手机。Streamlit 是服务端 Web 框架，
 * 界面由浏览器渲染、Python 服务端通过 websocket 驱动，无法打进 APK。
 * 所以手机与电脑必须在同一网络内。
 *
 * <p>地址只在首次启动时问一次，之后存在 SharedPreferences 里。
 * 连不上时给出可操作的提示，而不是白屏。
 */
public class MainActivity extends Activity {

    private static final String PREFS_NAME = "lifeng";
    private static final String KEY_URL = "server_url";

    private WebView webView;
    private ScrollView setupPanel;
    private LinearLayout errorPanel;
    private EditText urlInput;
    private TextView setupStatus;

    /** 主框架加载失败标记：避免子资源失败（如 favicon）误判成连不上。 */
    private boolean mainFrameFailed = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        webView = (WebView) findViewById(R.id.webview);
        setupPanel = (ScrollView) findViewById(R.id.setup_panel);
        errorPanel = (LinearLayout) findViewById(R.id.error_panel);
        urlInput = (EditText) findViewById(R.id.url_input);
        setupStatus = (TextView) findViewById(R.id.setup_status);
        Button connectButton = (Button) findViewById(R.id.connect_button);
        Button retryButton = (Button) findViewById(R.id.retry_button);
        Button changeUrlButton = (Button) findViewById(R.id.change_url_button);

        configureWebView();

        connectButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                connectFromInput();
            }
        });
        retryButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                loadSaved();
            }
        });
        changeUrlButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showSetup("");
            }
        });

        String saved = savedUrl();
        if (TextUtils.isEmpty(saved)) {
            showSetup("");
        } else {
            urlInput.setText(saved);
            load(saved);
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        // Streamlit 的界面完全依赖 JavaScript 与 WebSocket。
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setBuiltInZoomControls(false);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                if (!mainFrameFailed) {
                    showWebView();
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                // 只认主框架的错误。子资源（图标、字体）失败不代表服务没起来。
                if (request.isForMainFrame()) {
                    mainFrameFailed = true;
                    showError();
                }
            }
        });
    }

    private void connectFromInput() {
        String normalized = ServerUrl.normalize(urlInput.getText().toString());
        if (!ServerUrl.isLoadable(normalized)) {
            Toast.makeText(this, R.string.need_url, Toast.LENGTH_SHORT).show();
            return;
        }
        // 回写规范化结果：学生下次看到的就是可用格式
        urlInput.setText(normalized);
        prefs().edit().putString(KEY_URL, normalized).apply();
        load(normalized);
    }

    private void loadSaved() {
        String saved = savedUrl();
        if (TextUtils.isEmpty(saved)) {
            showSetup("");
        } else {
            load(saved);
        }
    }

    private void load(String url) {
        mainFrameFailed = false;
        setupStatus.setText(R.string.connecting);
        setupPanel.setVisibility(View.GONE);
        errorPanel.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl(url);
    }

    private void showWebView() {
        setupPanel.setVisibility(View.GONE);
        errorPanel.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
    }

    private void showSetup(String message) {
        mainFrameFailed = false;
        webView.setVisibility(View.GONE);
        errorPanel.setVisibility(View.GONE);
        setupPanel.setVisibility(View.VISIBLE);
        setupStatus.setText(message);
    }

    private void showError() {
        webView.setVisibility(View.GONE);
        setupPanel.setVisibility(View.GONE);
        errorPanel.setVisibility(View.VISIBLE);
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
    }

    private String savedUrl() {
        return prefs().getString(KEY_URL, "");
    }

    @Override
    public void onBackPressed() {
        // WebView 里有历史就回退，否则交给系统（退出 App）
        if (webView.getVisibility() == View.VISIBLE && webView.canGoBack()) {
            webView.goBack();
        } else if (webView.getVisibility() != View.VISIBLE) {
            showSetup("");
        } else {
            super.onBackPressed();
        }
    }
}
