# 企业 IT 运维知识助手

这是一个面向求职作品集的企业 IT 运维 Agent。当前 Task 1 提供可复现的本地开发基线；后续任务将逐步加入知识库检索、受控工单工具、LangGraph 编排、评测与可观测性。

## 本地启动

1. 复制环境变量模板：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 构建并启动服务：

   ```powershell
   docker compose up --build -d
   ```

3. 运行冒烟测试：

   ```powershell
   powershell -File scripts/smoke_test.ps1
   ```

4. 访问：

   - 前端：<http://localhost:5173>
   - API 健康检查：<http://localhost:18000/health>
   - Qdrant 调试端口：<http://localhost:16333>
   - PostgreSQL 调试端口：`localhost:15432`

## 本地测试

```powershell
cd backend
python -m pip install -e ".[dev]"
python -m pytest tests/unit/test_health.py -q
```

默认使用 `MODEL_MODE=mock`，Task 1 不需要任何外部模型 Key。
