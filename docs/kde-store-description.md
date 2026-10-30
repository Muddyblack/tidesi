[h1]tideSI[/h1]

[b]Claude, Codex, ChatGPT, GitHub Copilot, Gemini/Antigravity, Cursor, OpenCode, Ollama & more — AI usage, quotas and costs live in your KDE Plasma 6 panel.[/b]

Track Claude Code 5-hour and weekly limits, OpenAI Codex plan limits, Copilot premium requests, Cursor usage, and API balances for OpenRouter, DeepSeek, Kimi/Moonshot, Mistral, Grok/xAI, Z.AI, Kiro and Muse. One widget instead of an AI usage tab for each service.

I got tired of opening a different website or CLI tool every time I wanted to check how much quota I had left. So I built this: a little panel widget that puts every AI service I use right where I can see it — no tabs, no terminal, just a glance.

[b]One glance at your panel tells you exactly how much you've got left.[/b]

---

[b]What it tracks[/b]

Switch between tabs in the popup for each service:
[list]
[*] [b]Claude[/b] — 5-hour session + 7-day weekly windows
[*] [b]Antigravity / Google AI Studio[/b]
[*] [b]OpenAI API[/b]
[*] [b]Grok / xAI[/b]
[*] [b]Mistral AI[/b] — Key status, available models, and local vibe CLI cost and token stats
[*] [b]Kiro[/b] — Monthly credits, from the kiro-cli login or the Kiro IDE
[*] [b]OpenRouter[/b]
[*] [b]Z.AI[/b] — 5-hour token quota + monthly tools quota
[*] [b]GitHub Copilot[/b] — Premium request usage for personally billed plans, plus local Copilot CLI activity stats
[*] [b]DeepSeek[/b] — Current account balance + granted/topped-up split
[*] [b]Kimi[/b] — Kimi Code plan windows from the kimi CLI login, and/or the Moonshot API balance
[*] [b]Muse[/b] — Local Muse Code session stats with an offline spend estimate; plan windows behind an opt-in switch (Meta reports them only on a billed call, so it is off by default)
[*] [b]Cursor[/b] [i](free plan tested)[/i] — Included usage, Auto/API split and on-demand spend, via the cursor-agent or Cursor IDE login
[*] [b]OpenCode[/b] — Local token usage, costs and sessions in Zen mode; account usage windows in Go mode
[*] [b]Junie (untested)[/b] — Local CLI token activity, model breakdowns and resumable sessions; account quota and billed spend are unavailable
[*] [b]JetBrains AI[/b] — Monthly AI credits, top-up credits and refill date, read from the IDE's own quota file; no key or network
[*] [b]Windsurf[/b] — Daily and weekly quota from the editor's local cache; no key or network
[*] [b]Pi / OMP[/b] — Local token activity and model breakdowns from the agents' session files
[*] [b]Kilo[/b] — Prepaid credit balance and Kilo Pass usage via an API key or the kilo CLI login
[*] [b]CodeRabbit[/b] — Review count and reset date from the CodeRabbit CLI
[*] [b]Zed[/b] — Local Agent thread tokens on Linux, macOS and Windows; separate plan/edit-prediction quota via macOS Keychain, Linux Secret Service or Windows Credential Manager (live-verified on Linux only); thread cost is unavailable without per-request model attribution
[*] [b]MiMo Code[/b] — Local token usage, model breakdowns, session history and recorded or estimated costs
[*] [b]Ollama Cloud[/b] — Cloud usage windows, including weekly limits in the panel
[*] [b]Local Models[/b] — Monitor Ollama, vLLM and llama.cpp servers, with automatic endpoint discovery and runtime or GPU metrics where available
[/list]

[b]Why I like using it[/b]
[list]
[*] [b]Burn-rate ETA[/b] — tells you [i]"↗ ~3h to 100%"[/i] so you can pace yourself instead of getting surprised
[*] [b]Countdown timers[/b] — ticks down to your next quota reset (refreshes every ~5 min to stay friendly to the APIs)
[*] [b]Usage chart[/b] — a smooth, glowing trend graph with 5H / 24H / 7D toggle and hover-scrub (24H shows the whole day's session burn as a sawtooth)
[*] [b]Period comparison[/b] — [i]"+12% vs last week"[/i] at the same point in the cycle
[*] [b]Overview[/b] — see every enabled provider at a glance
[*] [b]Usage & Spend[/b] — Provider-reported spend and local token-based cost estimates, with daily cost and token charts, expandable histories and 1-day / 7-day / 30-day / all-history timeframes. Labels distinguish actual costs, estimates, subscription-covered usage and free models
[*] [b]Sessions[/b] — Recent local Claude Code, Codex, Grok CLI, Cline, OpenCode, MiMo Code, Junie, Antigravity, Mistral Vibe, Cursor, Muse and Zed Editor activity, with search, pagination and source filters. Cached results remain visible during background refresh. View local title previews and resume supported sessions in your terminal (Muse and Zed are display-only). Previews may contain sensitive text
[*] [b]Model pricing[/b] — Search cached model rates and refresh the pricing catalog from settings
[*] [b]Provider detection[/b] — Automatically detects installed providers on fresh installs, with a manual detection button in settings
[*] [b]Pick how each provider is read[/b] — Add providers from a search in Settings → Providers; each provider's page lists its sources (CLI, IDE, local server, API key) with a working indicator
[*] [b]Remote devices & sessions[/b] — Named Antigravity remote devices and Claude remote sessions, when accessible
[*] [b]Theme-aware[/b] — follows your Plasma accent by default, or flip on per-service brand colors
[*] [b]Glassmorphism popup[/b] — translucent, blurred, and honestly just nice to look at, with configurable popup decorations
[*] [b]Pin services[/b] — Keep your chosen providers visible in the panel, with optional automatic rotation between them
[*] [b]Project info[/b] — View version and release information, project statistics and useful links
[/list]

Enable the optional Overview, Usage & Spend and Sessions tabs in Settings → Views.

[b]Panel modes[/b]

Compact percentage readouts right in the taskbar — color-coded (amber at 70%, red at 90%) with an inline spark-line trend. Pill or compact mode, your call.

Optional panel rotation shows pinned providers one at a time in pin order. Choose an interval from 30 seconds to 10 minutes in Appearance; rotation is off by default.

---

[b]Setup[/b]

Reads supported credentials from local config files and existing CLI logins. Provider usage checks contact the relevant services; pricing and project information features also fetch online data. Refresh interval is configurable (1–30 min, default 5).

Credential notes: Z.AI uses the widget setting, [icode]$ZAI_TOKEN[/icode], or [icode]~/.config/zai/token[/icode]. GitHub Copilot needs no token on a machine already signed in: the Copilot plugin login ([icode]apps.json[/icode]), the Copilot CLI login, or [icode]gh auth token[/icode] is picked up automatically, and the widget setting or [icode]$GITHUB_TOKEN[/icode] still wins when set. A fine-grained token with Plan: read permission additionally unlocks GitHub's documented billing endpoint. The current user endpoint covers personally billed plans, not organization/enterprise-billed usage. DeepSeek uses the widget setting, [icode]$DEEPSEEK_API_KEY[/icode], or [icode]~/.config/deepseek/api-key[/icode]. Kiro, Kimi Code and Cursor need no key: they reuse the kiro-cli, kimi and cursor-agent logins.

OpenCode reads local session data and reuses its saved Zen/Go credentials; Zen activity reflects this device, while Go mode fetches account usage. Ollama Cloud uses a key from widget settings, [icode]$OLLAMA_API_KEY[/icode], or an existing OpenCode Ollama Cloud login. Local Models can auto-discover default Ollama, vLLM and llama.cpp endpoints, or use server URLs configured in settings.

[b]Requires Plasma 6.0+.[/b] tideSI also runs as a Hyprland/Quickshell shell, a tray app for Windows, macOS and other desktops, and a terminal CLI, all sharing the same backend.

---

🐙 Source, setup details & issues → [link=https://github.com/Muddyblack/tidesi]github.com/Muddyblack/tidesi[/link]

Built by [b]muddyblack[/b] • MIT licensed

[i](Yeah, I know, yet another AI usage tracker, but I wanted one for my own NixOS setup that just worked the way I needed it to. If you're in the same boat, here it is.)[/i] 💙
