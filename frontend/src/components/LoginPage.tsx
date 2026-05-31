import React, { useState } from "react";
import { Card, Form, Input, Button, Tabs, message } from "antd";
import { UserOutlined, LockOutlined } from "@ant-design/icons";
import { setToken, setUser } from "../lib/auth";

interface LoginPageProps {
  onLoginSuccess: () => void;
}

const LoginPage: React.FC<LoginPageProps> = ({ onLoginSuccess }) => {
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<"login" | "register">("login");

  const handleSubmit = async (values: {
    username: string;
    password: string;
  }) => {
    setLoading(true);
    try {
      const endpoint =
        activeTab === "login" ? "/api/auth/login" : "/api/auth/register";
      const resp = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      });

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(
          err.error?.message ||
            `${activeTab === "login" ? "登录" : "注册"}失败`,
        );
      }

      const data = await resp.json();
      setToken(data.token);
      setUser(data.user);
      message.success(activeTab === "login" ? "登录成功" : "注册成功");
      onLoginSuccess();
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: "100vh",
        background: "#f5f5f5",
      }}
    >
      <Card
        style={{ width: 400, boxShadow: "0 2px 8px rgba(0,0,0,0.1)" }}
      >
        <h2 style={{ textAlign: "center", marginBottom: 24 }}>
          DeepAgents 深度研搜
        </h2>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => setActiveTab(key as "login" | "register")}
          centered
          items={[
            { key: "login", label: "登录" },
            { key: "register", label: "注册" },
          ]}
        />
        <Form onFinish={handleSubmit} size="large">
          <Form.Item
            name="username"
            rules={[
              { required: true, message: "请输入用户名" },
              { min: 3, message: "用户名至少 3 个字符" },
              { max: 50, message: "用户名最多 50 个字符" },
            ]}
          >
            <Input prefix={<UserOutlined />} placeholder="用户名" />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[
              { required: true, message: "请输入密码" },
              { min: 6, message: "密码至少 6 个字符" },
            ]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              {activeTab === "login" ? "登录" : "注册"}
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
};

export default LoginPage;
