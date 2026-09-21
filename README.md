# Personal shell scripts

Collection of my script utilities for display brightness, screenshots, picture management,
PDF compression, and backups, plus shared output and color helpers. These scripts
target Linux and use GNU command-line utilities; use Bash 4 or newer.

## Contents

| File | Purpose |
| --- | --- |
| [archivePictureDir](#archivepicturedir) | Move picture directories into a personal archive. |
| [compress_pictures](#compress_pictures) | Resize and recompress JPEG images with ImageMagick. |
| [dar_wrapper](#dar_wrapper) | Create full and subsequent backups with DAR. |
| [ddcbrightness](#ddcbrightness) | Adjust external monitor backlight brightness over DDC/CI. |
| [flameshot_wrapper](#flameshot_wrapper) | Open Flameshot with a window or screen region selected. |
| [shrinkpdf](#shrinkpdf) | Reduce PDF size with qpdf, pdftk, and optionally Ghostscript. |
| [lib_output](#lib_output) | Shared status, diagnostic, and error functions. |
| [lib_colors](#lib_colors) | Terminal color and formatting variables for interactive Bash. |

## Setup

Run the executables directly from this checkout, for example `./ddcbrightness up`.
Install the dependencies listed below for the scripts you use.

`compress_pictures`, `dar_wrapper`, and `shrinkpdf` require `lib_output`, which
also loads `lib_colors`. From the repository directory, install the libraries:

```bash
sudo install -d /usr/local/lib
sudo install -m 644 lib_output lib_colors /usr/local/lib/
export PATH="$PWD:/usr/local/lib:$PATH"
```

The `PATH` entry for the repository makes the commands available from other
directories. The library entry lets `shrinkpdf` resolve its bare `source lib_output`
statement. Keep this checkout in place and add the equivalent absolute paths to
your shell configuration if you want the setup to persist.

For a user-only library installation, the loaders support a `BASH_PREFIX`:

```bash
export BASH_PREFIX="$HOME/.local/share/personal-scripts/"
mkdir -p "${BASH_PREFIX}usr/local/lib"
install -m 644 lib_output lib_colors "${BASH_PREFIX}usr/local/lib/"
export PATH="$PWD:${BASH_PREFIX}usr/local/lib:$PATH"
```

Keep the trailing slash in `BASH_PREFIX`; the scripts append `usr/local/lib` directly.

## Scripts

### archivePictureDir

[Source](archivePictureDir) · Dependencies: GNU `readlink`, `mkdir`, `mv`, `rmdir`.

Moves the non-hidden contents of each supplied directory into
`/home/$USER/Pictures/0.ARCHIV`, then attempts to remove the empty source directory.
The archive base must already exist; change `archivebase` in the script to use a
different location.

```bash
./archivePictureDir /home/alice/Pictures/Holidays
```

The destination is derived by stripping the first three components of the absolute
source path. For user `alice`, the example moves files into
`/home/alice/Pictures/0.ARCHIV/Holidays`.

This moves files rather than copying them. Hidden entries remain in the source,
preventing its removal. The move command uses unquoted paths, so directory names
containing spaces are not supported reliably.

### compress_pictures

[Source](compress_pictures) · Dependencies: ImageMagick's `identify` and `convert`,
`file`, and the shared libraries.

Processes JPEG files, optionally adjusting quality and fitting images inside a
square bounding box while preserving their aspect ratio.

```bash
# Keep originals and write resized images into compress_pictures.<PID>/
./compress_pictures --keep --resize 1920 --quality 85 photo.jpg

# Resize matching JPEGs in place
./compress_pictures --resize 1920 ./*.[jJ][pP][gG]
```

- `-r`, `--resize N`: fit within `N × N` pixels without enlarging smaller images.
- `-q`, `--quality N`: pass a quality percentage to ImageMagick.
- `-k`, `--keep`: leave originals untouched and retain output in the temporary directory.
- `-f`, `--force`: run conversion even when an image already fits the requested dimensions.
- `-v`, `--verbose`, `-d`, `--debug`, and `-h`, `--help`: diagnostics and usage.

With `--resize`, converted files replace originals unless `--keep` is set. Without
`--resize`, output remains in `compress_pictures.<PID>/` even without `--keep`.
Despite the built-in help text, the implementation does not compare output file
sizes before replacing originals. Non-JPEG files are skipped. Shell globs expand
before the script runs, so the example explicitly matches both `.jpg` and `.JPG`.

### dar_wrapper

[Source](dar_wrapper) · Dependencies: `dar`, `renice`, and the shared libraries.

Creates timestamped DAR backups and separate catalogs in an existing destination
directory. Archive names derive from the source path: `/home/alice` becomes
`home_alice`, and `/` becomes `rootfs`.

```bash
./dar_wrapper /home/alice /mnt/backup
./dar_wrapper --full /home/alice /mnt/backup
./dar_wrapper --exclude excluded-dirs.txt /home/alice /mnt/backup
./dar_wrapper --info /home/alice /mnt/backup
```

The first run creates a full backup. Later runs use the `.last.cat.1.dar` catalog
as their reference and update that link to the new catalog. The script calls these
backups “differential,” but each references the latest backup, forming an incremental
chain. Retain earlier archives needed for restoration.

- `-f`, `--full`: force a full backup.
- `-e`, `--exclude FILE`: read directory exclusion patterns, one per line.
- `-i`, `--info`: print configuration without creating a backup; source and target are still required.
- `-d`, `--debug`, and `-h`, `--help`: diagnostics and usage.

Defaults use bzip2 compression, stay on the source filesystem, exclude `*.dar` and
`*.swp`, and avoid recompressing common compressed formats. Adjust the configuration
block near the top for other defaults. Encryption is marked unsupported in the
script's help. Some generated commands do not quote paths, so use paths without spaces.

### ddcbrightness

[Source](ddcbrightness) · Dependencies: `ddcutil`, monitors supporting DDC/CI, and
permission to access them. `notify-send` is optional for desktop notifications.

Changes the physical backlight brightness of external monitors, useful alongside
the desktop's built-in controls for a laptop screen. By default, it detects all
DDC displays, changes brightness by 10, and clamps the result to 0–100.

```bash
./ddcbrightness up
./ddcbrightness --step 5 down
./ddcbrightness --display 1 up
```

- `-s`, `--step N`: set the step size; the intended range is 1–50.
- `-i`, `--display N`: target a particular `ddcutil` display number.
- `-d`, `--debug`, and `-h`, `--help`: diagnostics and usage.

Bind `ddcbrightness down` and `ddcbrightness up` to desktop shortcuts such as
`Super+Alt+F3` and `Super+Alt+F4` to control external displays from the keyboard.

### flameshot_wrapper

[Source](flameshot_wrapper) · Dependencies: `flameshot`, `xdotool`, and an X11 session.

Opens the Flameshot screenshot GUI with a region derived from window geometry.

```bash
./flameshot_wrapper activewindow   # Use the active window's bounds
./flameshot_wrapper selectwindow   # Click a window to use its bounds
./flameshot_wrapper                # Pass screen<N> as the region
```

With no argument (or an unrecognized argument), `N` comes from
`xdotool get_desktop`. This is the virtual desktop index, so it may not identify
the intended physical monitor on a multi-monitor setup. The wrapper has no help flag.

### shrinkpdf

[Source](shrinkpdf) · Dependencies: `qpdf`, `pdftk`, Ghostscript (`gs`), `bc`, `file`,
GNU `du`, `awk`, and the shared libraries. `gs` must be installed even when no
Ghostscript preset is selected.

Runs PDFs through qpdf and pdftk, compares the resulting sizes, and normally replaces
the original only when a smaller result is available. Files must have a `.pdf` or
`.PDF` extension and be recognized as PDFs by `file`. Arguments containing
`noshrink` are skipped.

```bash
./shrinkpdf document.pdf
./shrinkpdf --keep --pdfsetting ebook scan.pdf
./shrinkpdf --verbose ./*.pdf
```

- `-k`, `--keep`: rename the original to `<file>_orig` before choosing the output.
- `-p`, `--pdfsetting PRESET`: run Ghostscript first, using `default`, `prepress`,
  `printer`, `ebook`, or `screen`. This can reduce image quality.
- `-v`, `--verbose`, and `-h`, `--help`: diagnostics and usage.

With `--keep`, if neither compressed candidate is smaller, the original remains
under `<file>_orig`; it is not restored to its original filename. Ghostscript
intermediate files are also retained with this option.

## Shared libraries

### lib_output

[Source](lib_output) · Dependency: `lib_colors` in the configured library directory.

Source this file from Bash to use `success`, `warn`, `error`, `debug`, and their
`_n` variants, which omit the trailing newline. Messages go to standard error;
debug output is enabled by a non-empty `opt_debug`. `die` reports an error and exits
with the preceding command's status, so it does not always return a nonzero code.

```bash
source /usr/local/lib/lib_output
success "Finished"
opt_debug=1
debug "Extra detail"
```

### lib_colors

[Source](lib_colors) · Dependency: `tput` and a suitable terminal definition.

Source this file in interactive Bash to export terminal formatting variables such
as `COLOR_RED`, `COLOR_GREEN`, `COLOR_BOLD`, and `COLOR_NONE` (reset). It prints a
loading message in interactive shells and returns without initializing colors in
non-interactive shells.

```bash
source /usr/local/lib/lib_colors
printf '%sHello%s\n' "$COLOR_GREEN" "$COLOR_NONE"
```
