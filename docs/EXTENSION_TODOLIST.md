# DeepAgents 扩展方向 Todo List

## 已完成
- [x] PostgreSQL + Redis 持久化存储
- [x] 用户认证（JWT）
- [x] 用户组数据隔离
- [x] 任务队列化（ARQ）
- [x] 结构化日志（structlog）
- [x] 容器化生产部署（Docker + Nginx + docker compose）

## 短期（1-2 周）
- [ ] MySQL 连接改为异步（aiomysql），替换 mysql-connector-python
- [ ] 前端 SSE 替代部分轮询，降低 WebSocket 重连开销
- [ ] 文件对象存储迁移准备（MinIO / S3 兼容）
- [ ] 前端暗色模式支持

## 中期（1 个月）
- [ ] OpenTelemetry 链路追踪（Traces + Metrics 接入 Jaeger / Grafana）
- [ ] 多 Worker 分布式部署（跨节点 ARQ + Redis Cluster）
- [ ] 会话分享功能（生成分享链接 + 只读视图）
- [ ] 自定义提示词模板（per-group / per-user）
- [ ] 知识库管理 UI（前端直接管理文档摄入、删除、统计）

## 长期
- [ ] 可视化工具编排（拖拽式 Agent + Tool 工作流）
- [ ] 协同批注（多用户对同一报告标注讨论）
- [ ] 多语言国际化（i18n，优先英文 + 中文）
- [ ] 模型评测对比平台（A/B 对比不同 LLM 在同一任务的表现）
- [ ] 细粒度 RBAC 权限（角色 → 资源 → 操作三级控制）
- [ ] Kubernetes Helm Chart 部署支持
