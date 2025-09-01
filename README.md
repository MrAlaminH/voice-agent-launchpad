# Voice Agents launchpad - LiveKit

A complete starter project for building voice AI apps with [LiveKit Agents for Python](https://github.com/livekit/agents).

## Dev Setup

Clone the repository and install dependencies to a virtual environment:

```console
cd Voice-Agent-Starter
uv sync
```

Set up the environment by copying `.env.example` to `.env.local` and filling in the required values:

```bash
lk app env -w .env.local
```

## Run the agent

```console
uv run python src/agent.py download-files
```

Next, run this command to speak to your agent directly in your terminal:

```console
uv run python src/agent.py console
```

To run the agent for use with a frontend or telephony, use the `dev` command:

```console
uv run python src/agent.py dev
```

```console
python -m src.agent dev
```

In production, use the `start` command:

```console
uv run python src/agent.py start
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
