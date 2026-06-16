import "antd/dist/reset.css";
import { App as AntApp, ConfigProvider, theme } from "antd";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
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
            "'JetBrains Mono', 'SFMono-Regular', Consolas, 'Liberation Mono', monospace"
        },
        components: {
          Button: {
            controlHeightLG: 46,
            primaryShadow: "0 0 24px rgba(74, 143, 216, 0.22)"
          },
          Input: {
            activeBorderColor: "#4a8fd8",
            hoverBorderColor: "#22c55e"
          }
        }
      }}
    >
      <AntApp>
        <App />
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>
);
