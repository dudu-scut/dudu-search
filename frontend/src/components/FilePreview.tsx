import { DownloadOutlined } from "@ant-design/icons";
import { Button, Modal, Spin, Typography } from "antd";
import { useEffect, useState } from "react";

const { Text } = Typography;

interface FilePreviewProps {
  fileName: string;
  fileUrl: string;
  open: boolean;
  onClose: () => void;
}

type FileType = "image" | "markdown" | "pdf" | "other";

function detectFileType(name: string): FileType {
  const lower = name.toLowerCase();
  if (/\.(png|jpe?g|gif|webp|svg)$/.test(lower)) return "image";
  if (/\.(md|txt)$/.test(lower)) return "markdown";
  if (/\.pdf$/.test(lower)) return "pdf";
  return "other";
}

function ImagePreview({ url, name }: { url: string; name: string }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  return (
    <div style={{ textAlign: "center" }}>
      {loading && !error ? (
        <div style={{ padding: 40 }}>
          <Spin tip="加载中..." />
        </div>
      ) : null}
      {error ? (
        <div style={{ padding: 40, color: "var(--muted)" }}>
          图片加载失败，请点击下载按钮查看
        </div>
      ) : (
        <img
          alt={name}
          onError={() => {
            setError(true);
            setLoading(false);
          }}
          onLoad={() => setLoading(false)}
          src={url}
          style={{
            display: error ? "none" : "block",
            maxWidth: "100%",
            maxHeight: "70vh",
            objectFit: "contain",
            margin: "0 auto",
          }}
        />
      )}
    </div>
  );
}

function MarkdownPreview({ url }: { url: string }) {
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      })
      .then((text) => {
        if (!cancelled) {
          setContent(text);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [url]);

  if (loading) {
    return (
      <div style={{ padding: 40, textAlign: "center" }}>
        <Spin tip="加载中..." />
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 40, textAlign: "center", color: "var(--red)" }}>
        加载失败: {error}
      </div>
    );
  }

  return (
    <pre
      style={{
        maxHeight: "65vh",
        overflow: "auto",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        background: "var(--bg)",
        padding: 16,
        borderRadius: 8,
        fontSize: 13,
        lineHeight: 1.7,
      }}
    >
      {content}
    </pre>
  );
}

function PdfPreview({ url }: { url: string }) {
  return (
    <iframe
      src={url}
      style={{
        width: "100%",
        height: "70vh",
        border: "none",
        borderRadius: 8,
      }}
      title="PDF 预览"
    />
  );
}

function UnsupportedPreview() {
  return (
    <div
      style={{
        padding: 60,
        textAlign: "center",
        color: "var(--muted)",
      }}
    >
      <Text type="secondary">
        暂不支持此文件类型的在线预览，请点击下载按钮查看。
      </Text>
    </div>
  );
}

export default function FilePreview({
  fileName,
  fileUrl,
  open,
  onClose,
}: FilePreviewProps) {
  const fileType = detectFileType(fileName);

  function renderPreview() {
    switch (fileType) {
      case "image":
        return <ImagePreview url={fileUrl} name={fileName} />;
      case "markdown":
        return <MarkdownPreview url={fileUrl} />;
      case "pdf":
        return <PdfPreview url={fileUrl} />;
      default:
        return <UnsupportedPreview />;
    }
  }

  return (
    <Modal
      footer={[
        <Button
          key="download"
          href={fileUrl}
          icon={<DownloadOutlined />}
          type="primary"
        >
          下载文件
        </Button>,
        <Button key="close" onClick={onClose}>
          关闭
        </Button>,
      ]}
      onCancel={onClose}
      open={open}
      title={
        <Text ellipsis style={{ maxWidth: 400 }}>
          {fileName}
        </Text>
      }
      width={fileType === "image" ? 800 : 900}
    >
      {renderPreview()}
    </Modal>
  );
}
