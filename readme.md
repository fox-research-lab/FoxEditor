# 🦊 FoxEditor

FoxEditor is a lightweight **vi-like GUI text editor** built with **Python** and **tkinter**.

It combines classic modal editing concepts inspired by vi with a modern cyber-style user interface.

The project is also an experiment in integrating **local LLMs** with a custom editor.

Current version: **v0.6**

---

# Features

## Vi-like Editing

FoxEditor supports modal editing similar to vi.

### Normal Mode

- Navigation and commands
- Green border
- Safe browsing mode

### Insert Mode

- Text editing
- Red border
- Direct text input

---

## Lightweight GUI

Built entirely with:

- Python
- tkinter

No external GUI frameworks required.

---

## Local LLM Integration

Starting from v0.5, FoxEditor can connect to a local Ollama server and use:

```text
Gemma4:e2b
```

for source code summarization.

Command:

```text
:gemma4_smr
```

The editor sends up to 3000 lines of code and displays the generated summary in a popup window.

---

## Line Numbers

Added in v0.6.

Command:

```text
:set num
```

Displays synchronized line numbers using a dedicated Canvas-based component.

---

# Installation

## Requirements

- Python 3.10+
- tkinter
- Windows

Optional:

- Ollama
- Gemma4:e2b

---

## Clone Repository

```bash
git clone https://github.com/fox-research-lab/FoxEditor.git
cd FoxEditor
```

---

## Run

```bash
python foxeditor.py
```

Open a file:

```bash
python foxeditor.py sample.txt
```

---

# Key Bindings

| Key | Action |
|-------|-------|
| h | Move left |
| j | Move down |
| k | Move up |
| l | Move right |
| i | Enter Insert Mode |
| x | Delete character |
| 0 | Move to beginning of line |
| $ | Move to end of line |
| o | Open new line below |
| u | Undo |
| dd | Delete line |
| yy | Copy line |
| p | Paste line |
| / | Search |
| n | Next match |
| N | Previous match |
| Ctrl+D | Scroll down |
| Ctrl+U | Scroll up |
| G | Last line |
| number + G | Jump to line |

---

# Commands

| Command | Description |
|-----------|-----------|
| :w | Save file |
| :q | Quit |
| :q! | Force quit |
| :wq | Save and quit |
| :set num | Show line numbers |
| :gemma4_smr | Summarize code with Gemma4 |

---

# Architecture

```text
FoxEditor
├── SearchManager
├── EditCommands
├── FileManager
├── KeyDispatcher
├── AIManager
├── LineNumberArea
└── FoxEditor
```

### Responsibilities

| Class | Responsibility |
|---------|---------|
| SearchManager | Search and highlight |
| EditCommands | Editing operations |
| FileManager | File I/O |
| KeyDispatcher | Key event routing |
| AIManager | LLM integration |
| LineNumberArea | Line number rendering |
| FoxEditor | Main application |

---

# AI Integration

FoxEditor v0.5 introduced local AI support.

Workflow:

```text
FoxEditor
     │
     ▼
Ollama API
     │
     ▼
Gemma4:e2b
     │
     ▼
Code Summary Popup
```

The implementation uses:

- daemon threads
- localhost API calls
- tkinter `after()`
- non-blocking UI updates

This allows the editor to remain responsive while AI requests are running.

---

# Version History

## v0.1

Initial release.

Features:

- File open
- File save
- Normal mode
- Insert mode

---

## v0.2

Search support.

Added:

- /
- n
- N

Search highlighting implemented.

---

## v0.3

Line editing commands.

Added:

- dd
- yy
- p

---

## v0.33

Navigation improvements.

Added:

- Ctrl+D
- Ctrl+U
- G
- number + G

---

## v0.5

Local AI integration.

Added:

```text
:gemma4_smr
```

Features:

- Ollama integration
- Gemma4:e2b support
- Code summarization
- Popup display

---

## v0.6

Vi-like functionality expansion.

Added:

- 0
- $
- o
- u
- :set num

New component:

```text
LineNumberArea
```

---

# Roadmap

FoxEditor v0.6 includes most of the core vi-like functionality originally planned.

The editor is currently considered feature-complete for its initial scope.

Future development may focus on AI-assisted features if local LLM technology becomes significantly more practical.

Potential future features:

- Code review
- Refactoring suggestions
- Bug detection
- Function explanations
- Documentation generation

---

# Philosophy

FoxEditor is not intended to compete with Vim, Neovim, VS Code, or other mature editors.

Instead, it is a personal experiment exploring:

- Modal editing
- GUI programming with tkinter
- Software architecture
- Local LLM integration
- AI-assisted development

---

# License

MIT License

---

# Author

**Fox Research Lab**

Embedded Software Engineer, Physics Enthusiast, and AI Explorer.

Development logs and articles are published on note.