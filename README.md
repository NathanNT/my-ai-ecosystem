# my-ai-ecosystem

Resources for setting up my hybrid AI ecosystem, combining local models and models running on rented GPUs with cloud providers, orchestrated through agent harnesses. Includes usage tracking, skills, MCP integrations, documentation, and environment configuration.

## Layout

- `skills/`: reusable skills shared across agent harnesses.
- `harnesses/`: harness-specific configuration and instructions.
- `harnesses/qwen-code/`: Qwen Code setup for the RunPod vLLM infrastructure.
- `mcp/`: MCP-related resources and configuration examples.
- `monitoring/`: future usage, GPU, and cost tracking resources.
- `scripts/`: Windows-first PowerShell scripts and portable Python automation.
- `docs/`: architecture and restoration notes.
- `config/`: shared configuration examples without secrets.

Windows shortcuts are available in `scripts/`: use `start-runpod-dashboard.bat` to launch the RunPod dashboard, `runpod-monitor.bat` to list pods, and `openclaw-status.bat` / `openclaw-gateway.bat` for OpenClaw.
