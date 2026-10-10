package com.lifeng.app;

/**
 * 服务器地址的规范化。
 *
 * <p>单独成类是为了**可测**：这是安卓端唯一含有真实逻辑的部分，
 * 其余都是 WebView 的摆位。放在 Activity 里就只能靠人装到手机上试，
 * 放在这里可以用 JUnit 在 CI 的 JVM 上直接验。
 *
 * <p>学生输入的东西五花八门：可能只写 IP、可能带末尾斜杠、
 * 可能漏了端口。这里统一成一个可加载的 URL。
 */
final class ServerUrl {

    /** 砺锋默认监听的端口，与 Streamlit 一致。 */
    static final String DEFAULT_PORT = "8501";

    private ServerUrl() {
    }

    /**
     * 把用户输入规范成完整 URL。
     *
     * <pre>
     *   192.168.1.10            -> http://192.168.1.10:8501
     *   http://192.168.1.10     -> http://192.168.1.10:8501
     *   192.168.1.10:9000       -> http://192.168.1.10:9000
     *   http://host:8501/       -> http://host:8501
     *   https://example.com     -> https://example.com   （不补端口）
     *   ""                      -> ""
     * </pre>
     *
     * @return 规范化的 URL；输入为空或不可用时返回空串。
     */
    static String normalize(String raw) {
        if (raw == null) {
            return "";
        }
        String url = raw.trim();
        if (url.isEmpty()) {
            return "";
        }

        boolean https = url.regionMatches(true, 0, "https://", 0, 8);
        boolean http = url.regionMatches(true, 0, "http://", 0, 7);
        if (!https && !http) {
            url = "http://" + url;
        }

        // 去掉末尾斜杠：Streamlit 对 / 与不带 / 都能响应，
        // 但统一形式便于比对与写回输入框。
        while (url.length() > 8 && url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }

        // 只有 http 且没写端口时才补默认端口。
        // https 通常走 443，硬补 8501 反而会连不上。
        if (!https) {
            url = withDefaultPort(url);
        }
        return url;
    }

    /**
     * 若 authority 部分没有端口，则补上 {@link #DEFAULT_PORT}。
     *
     * <p>IPv6 字面量（形如 {@code [::1]:8501}）按“已带端口”处理，
     * 因为方括号内的冒号不是端口分隔符。
     */
    static String withDefaultPort(String url) {
        int schemeEnd = url.indexOf("://");
        if (schemeEnd < 0) {
            return url;
        }
        int authorityStart = schemeEnd + 3;
        int slash = url.indexOf('/', authorityStart);
        String authority = slash < 0 ? url.substring(authorityStart) : url.substring(authorityStart, slash);
        String tail = slash < 0 ? "" : url.substring(slash);

        if (authority.isEmpty() || authority.contains(":")) {
            return url;
        }
        return url.substring(0, authorityStart) + authority + ":" + DEFAULT_PORT + tail;
    }

    /** 地址是否已经足够可信，可以直接尝试加载。 */
    static boolean isLoadable(String normalized) {
        if (normalized == null || normalized.isEmpty()) {
            return false;
        }
        String rest = normalized;
        int schemeEnd = rest.indexOf("://");
        if (schemeEnd < 0) {
            return false;
        }
        rest = rest.substring(schemeEnd + 3);
        int slash = rest.indexOf('/');
        if (slash >= 0) {
            rest = rest.substring(0, slash);
        }
        return !rest.isEmpty();
    }
}
