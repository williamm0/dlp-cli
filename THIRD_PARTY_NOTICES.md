# Third-party notices

DLP bundles or depends on these projects. The release process must include the license files shipped by the installed packages alongside the application artifact.

| Project | License | Source |
| --- | --- | --- |
| yt-dlp | Unlicense, with licenses for bundled components | https://github.com/yt-dlp/yt-dlp |
| yt-dlp-ejs | Unlicense, MIT, and ISC components | https://github.com/yt-dlp/ejs |
| Textual | MIT | https://github.com/Textualize/textual |
| platformdirs | MIT | https://github.com/tox-dev/platformdirs |
| tomli-w | MIT | https://github.com/hukkin/tomli-w |
| PyInstaller | GPLv2 with the PyInstaller bootloader exception | https://github.com/pyinstaller/pyinstaller |

The build scripts run `packaging/collect_licenses.py` and include the resulting `licenses/MANIFEST.txt` and license files inside each frozen artifact. DLP does not bundle ffmpeg or Deno; users install those tools separately under the prompts described in the README.
