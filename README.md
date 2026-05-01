# gh-archive-gui

A simple PyQt5 GUI wrapper around the `iagitup` CLI tool for archiving GitHub repositories to the Internet Archive.

## Features

- Graphical interface for archiving GitHub repos
- Configurable download/upload limits
- Automatic detection of archived URLs
- Persistent configuration storage

## Installation

### Prerequisites

- Python 3.6+
- PyQt5
- iagitup (install via pip: `pip3 install --user iagitup`)

### Install Dependencies

```bash
pip3 install PyQt5 iagitup
```

### Run the Application

```bash
python3 gh-archive.py
```

## Usage

1. Enter the GitHub repository URL (e.g., https://github.com/user/repo)
2. Enter your Internet Archive S3 access key and secret key
3. Optionally set download and upload limits
4. Click "Archive" to start the process
5. View the output in the text box
6. Once archived, click "Open on archive.org" to view the result

## Configuration

Settings are automatically saved to `~/.gh-archive/config.json`.

Internet Archive credentials are stored in `~/.config/internetarchive/ia.ini`.

## License

MIT License - see LICENSE file for details.