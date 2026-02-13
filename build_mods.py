import os
import shutil
import subprocess
import zipfile
from pathlib import Path
import ctypes

# --- Paths / folders ---
builder_dir = Path(__file__).resolve().parent
mods_dir = builder_dir / "mods"
output_dir = builder_dir / "output"
game_dir = builder_dir.parent

# Only these top-level folders are managed by this tool.
MANAGED_TOP_FOLDERS = {"audio", "data", "levels", "shaders", "swfs", "textures"}

def _is_windows_reparse_point(path: Path) -> bool:
    """True for junctions/symlinks (reparse points) on Windows."""
    if os.name != "nt":
        return False

    FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
    INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    GetFileAttributesW = kernel32.GetFileAttributesW
    GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    GetFileAttributesW.restype = ctypes.c_uint32

    attrs = GetFileAttributesW(str(path))
    if attrs == INVALID_FILE_ATTRIBUTES:
        return False
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def _remove_existing_path(p: Path) -> None:
    """Remove file/dir/junction/symlink at p without deleting link targets."""
    # Handle broken links/junctions too: exists() can be False.
    if not p.exists() and not p.is_symlink() and not _is_windows_reparse_point(p):
        return

    if p.is_symlink():
        p.unlink()
        return

    if p.is_dir():
        if _is_windows_reparse_point(p):
            os.rmdir(p)   # removes the junction itself; does NOT touch the target
            return
        shutil.rmtree(p)
        return

    p.unlink()



def _try_create_dir_link(src_dir: Path, dest: Path) -> str:
    """
    Create a link at dest pointing to src_dir.

    Preferred order:
    1) symlink (fast, ideal, but may require admin/dev mode on Windows)
    2) junction (Windows-only, usually no admin required)
    3) copy (always works)

    Returns a string describing what was done: "symlink", "junction", or "copied".
    """
    src_dir = src_dir.resolve()
    try:
        os.symlink(src_dir, dest, target_is_directory=True)
        return "symlink"
    except OSError:
        if os.name != "nt":
            raise

    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dest), str(src_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        return "junction"
    except Exception:
        shutil.copytree(src_dir, dest)
        return "copied"


def symlink_to_game():
    """
    Deploy step: for each top-level folder inside output/, create a link in game_dir.

    Windows note:
    - Symlinks can require admin rights or Developer Mode.
    - This function falls back to junctions (mklink /J) and then copying if needed.
    """

    for item in output_dir.iterdir():
        dest = game_dir / item.name
        _remove_existing_path(dest)

        if item.is_dir():
            method = _try_create_dir_link(item, dest)
            print(f"Deployed {item.name}: {method} -> {dest}")
        else:
            shutil.copy2(item, dest)
            print(f"Deployed {item.name}: copied file -> {dest}")


def clear_output():
    """
    Ensure output/ is a clean empty folder.
    """
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)


def copy_tree(src: Path, dst: Path):
    """
    Recursively copy all files from src -> dst, preserving folder structure.

    This is similar to shutil.copytree(), but:
    - It merges into an existing destination folder.
    - It will overwrite files if they already exist in dst.
    - It preserves file metadata (timestamps, etc.) via shutil.copy2().
    """
    # os.walk yields (current_folder_path, list_of_subfolders, list_of_files)
    for root, dirs, files in os.walk(src):
        # Compute path relative to the src root, so we can mirror structure in dst.
        rel_path = Path(root).relative_to(src)

        # Target folder in dst that corresponds to this src folder.
        target_root = dst / rel_path
        target_root.mkdir(parents=True, exist_ok=True)

        # Copy each file in this folder into the corresponding target folder.
        for file in files:
            src_file = Path(root) / file
            dst_file = target_root / file
            shutil.copy2(src_file, dst_file)  # copy2 keeps metadata (mtime, etc.)


def extract_zip(zip_path: Path, temp_dir: Path):
    """
    Unpack a .zip mod into temp_dir.

    The extracted files are later copied into output/ to merge with other mods.
    """
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(temp_dir)


def build_mods():
    """
    Build step: merge all mods from mods/ into output/.

    Rules:
    - If an entry inside mods/ is a directory: copy its entire contents into output/
    - If an entry is a .zip file: extract it into a temporary folder, then copy into output/

    Ordering:
    - mods are processed in sorted order by name.
    - If multiple mods contain the same file path, whichever is processed later overwrites earlier ones.
    """
    clear_output()

    # sorted() gives repeatable results across runs (important when overwrites happen).
    for mod in sorted(mods_dir.iterdir()):
        print(f"Processing mod: {mod.name}")

        if mod.is_dir():
            # Directly merge folder mod into output/
            copy_tree(mod, output_dir)

        elif mod.suffix == ".zip":
            # For zip mods, extract first, then merge extracted content.
            temp_dir = output_dir / "__temp__"
            temp_dir.mkdir(exist_ok=True)

            extract_zip(mod, temp_dir)
            copy_tree(temp_dir, output_dir)

            # Clean up the temp extraction folder after merging.
            shutil.rmtree(temp_dir)

def undeploy_from_game():
    """
    Remove only the items that this tool would deploy into the game folder.

    Safety rule:
    - We never delete `game_dir`.
    - We only remove top-level entries whose names match entries in output/.
    """
    if not output_dir.exists():
        print("Nothing to undeploy: output/ does not exist yet.")
        return

    removed_any = False
    for item in MANAGED_TOP_FOLDERS: #output_dir.iterdir():

        print(f"Checking for {item}...")
        dest = game_dir / item
        print(f"Destination: {dest}")


        if dest.exists() or dest.is_symlink():
            print(f"Found deployed item: {dest}")
            print(dest, dest.exists(), dest.is_dir(), dest.is_symlink(), _is_windows_reparse_point(dest))
            _remove_existing_path(dest)
            print(f"Removed from game: {dest}")
            removed_any = True

    if not removed_any:
        print("No deployed items found to remove.")

if __name__ == "__main__":
    action = input("[B]uild and merge mods, [D]elete excess folders, or [Q]uit?").lower().strip()

    if action == "b":
        build_mods()
        symlink_to_game()
        print("Mod merge complete.")
    elif action == "d":
        undeploy_from_game()


    elif action == "q":
        print("Quitting.")
        exit(0)