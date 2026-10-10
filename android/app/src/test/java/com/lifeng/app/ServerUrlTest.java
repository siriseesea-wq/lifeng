package com.lifeng.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * {@link ServerUrl} 的单元测试。
 *
 * <p>跑在 JVM 上（{@code gradle test}），不需要模拟器或真机 ——
 * 所以 CI 能真的验证这部分逻辑，而不是只说"编译通过"。
 */
public class ServerUrlTest {

    @Test
    public void bareIpGetsSchemeAndPort() {
        assertEquals("http://192.168.1.10:8501", ServerUrl.normalize("192.168.1.10"));
    }

    @Test
    public void missingPortIsFilled() {
        assertEquals("http://192.168.1.10:8501", ServerUrl.normalize("http://192.168.1.10"));
    }

    @Test
    public void explicitPortIsKept() {
        assertEquals("http://192.168.1.10:9000", ServerUrl.normalize("192.168.1.10:9000"));
    }

    @Test
    public void trailingSlashIsRemoved() {
        assertEquals("http://host:8501", ServerUrl.normalize("http://host:8501/"));
        assertEquals("http://host:8501", ServerUrl.normalize("http://host:8501///"));
    }

    @Test
    public void httpsKeepsItsOwnPort() {
        // 给 https 硬补 8501 会直接连不上，所以不补
        assertEquals("https://example.com", ServerUrl.normalize("https://example.com"));
    }

    @Test
    public void httpsWithExplicitPortIsKept() {
        assertEquals("https://example.com:8443", ServerUrl.normalize("https://example.com:8443"));
    }

    @Test
    public void surroundingWhitespaceIsTrimmed() {
        assertEquals("http://10.0.0.2:8501", ServerUrl.normalize("  10.0.0.2  "));
    }

    @Test
    public void emptyInputStaysEmpty() {
        assertEquals("", ServerUrl.normalize(""));
        assertEquals("", ServerUrl.normalize("   "));
        assertEquals("", ServerUrl.normalize(null));
    }

    @Test
    public void ipv6LiteralIsNotGivenAFakePort() {
        // 方括号里的冒号不是端口分隔符
        assertEquals("http://[::1]:8501", ServerUrl.normalize("http://[::1]:8501"));
    }

    @Test
    public void pathIsPreserved() {
        assertEquals("http://host:8501/app", ServerUrl.normalize("host/app"));
    }

    @Test
    public void loadableCheck() {
        assertTrue(ServerUrl.isLoadable(ServerUrl.normalize("192.168.1.10")));
        assertFalse(ServerUrl.isLoadable(""));
        assertFalse(ServerUrl.isLoadable("http://"));
    }
}
