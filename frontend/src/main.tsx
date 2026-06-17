import "antd/dist/reset.css";
import { App as AntApp, ConfigProvider, theme } from "antd";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ThemeProvider, useTheme } from "./hooks/ThemeContext";
import "./styles.css";

/** 浅色主题 Ant Design token 配置 */
const lightTokens = {
  colorPrimary: "#4a8fd8",
  colorSuccess: "#22c55e",
  colorWarning: "#f59e0b",
  colorError: "#ef4444",
  colorInfo: "#6366f1",
  colorBgBase: "#f8f6f2",
  colorBgContainer: "rgba(255, 253, 250, 0.96)",
  colorBorder: "rgba(74, 143, 216, 0.16)",
  borderRadius: 14,
  fontFamily:
    "'Plus Jakarta Sans', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
  fontFamilyCode:
    "'JetBrains Mono', 'SFMono-Regular', Consolas, 'Liberation Mono', monospace",
};

/** 深色主题 Ant Design token 配置 */
const darkTokens = {
  colorPrimary: "#5a9de8",
  colorSuccess: "#22c55e",
  colorWarning: "#f59e0b",
  colorError: "#ef4444",
  colorInfo: "#818cf8",
  colorBgBase: "#1a1a2e",
  colorBgContainer: "#242438",
  colorBorder: "rgba(90, 157, 232, 0.18)",
  borderRadius: 14,
  fontFamily: lightTokens.fontFamily,
  fontFamilyCode: lightTokens.fontFamilyCode,
};

/** 内部组件 — 读取主题状态并动态配置 Ant Design ConfigProvider */
function ThemedApp() {
  const { isDark } = useTheme();

  return (
    <ConfigProvider
      theme={{
        algorithm: isDark ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: isDark ? darkTokens : lightTokens,
        components: {
          Button: {
            controlHeightLG: 46,
            primaryShadow: isDark
              ? "0 0 24px rgba(90, 157, 232, 0.16)"
              : "0 0 24px rgba(74, 143, 216, 0.22)",
          },
          Input: {
            activeBorderColor: isDark ? "#5a9de8" : "#4a8fd8",
            hoverBorderColor: "#22c55e",
          },
        },
      }}
    >
      <AntApp>
        <App />
      </AntApp>
    </ConfigProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <ThemedApp />
    </ThemeProvider>
  </React.StrictMode>
);
