# Get "IFC Merge.exe" without installing Python

GitHub builds it for you on a Windows machine. Takes about 5 minutes, once.

## Steps

1. **Make a repo.** Go to github.com (free account is fine) → "New repository" → name it `ifc-merge`,
   keep it Private, click Create.

2. **Upload the files.** On the empty repo page click "uploading an existing file".
   Drag ALL files and folders from this zip into the browser window, including the hidden `.github` folder
   (on Windows: in Explorer, View → Show → Hidden items, then select everything).
   Click "Commit changes".

   > If the `.github` folder does not upload (browsers sometimes skip hidden folders): in the repo click
   > "Add file" → "Create new file", type the name `.github/workflows/build-exe.yml` and paste the content
   > of that file from the zip. Commit.

3. **Run the build.** Click the "Actions" tab → "Build IFC Merge.exe" (left side) → "Run workflow" → green button.
   Wait until the run shows a green check (about 4–6 minutes). The first time, GitHub may ask you to enable Actions; click yes.

4. **Download.** Click the finished run → scroll down to "Artifacts" → click `IFC-Merge-windows`.
   You get a zip with `IFC Merge.exe` inside. Copy the exe anywhere, double-click, done.

## Notes

- Windows SmartScreen may warn the first time because the exe is not signed. Click "More info" → "Run anyway".
- Every time you change a file in the repo, GitHub builds a fresh exe automatically.
- The exe is about 60–90 MB because it contains Python and IfcOpenShell. Normal.
- Artifacts are kept 90 days. Download it once and store it on the KCAP server.
