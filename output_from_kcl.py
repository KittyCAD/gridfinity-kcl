import asyncio
import os
import re
from concurrent.futures import ProcessPoolExecutor
from io import BytesIO
from pathlib import Path

import kcl
import requests
from PIL import Image

RETRIES = 5


def export_step(kcl_path: Path, save_path: Path) -> bool:
    # determine the current directory
    try:
        export_response = asyncio.run(
            kcl.execute_and_export(str(kcl_path.parent), kcl.FileExportFormat.Step)
        )

        stl_path = save_path.with_suffix(".step")

        with open(stl_path, "wb") as out:
            out.write(bytes(export_response[0].contents))

        return True
    except Exception as e:
        print(e)
        return False


def find_files(
        path: str | Path, valid_suffixes: list[str], name_pattern: str | None = None
) -> list[Path]:
    """
    Recursively find files in a folder by a list of provided suffixes or file naming pattern

    Args:
        path: str | Path
            Root folder to search
        valid_suffixes: Container[str]
            List of valid suffixes to find files by (e.g. ".stp", ".step")
        name_pattern: str
            Name pattern to additionally filter files by (e.g. "_component")

    Returns:
        list[Path]
    """
    path = Path(path)
    valid_suffixes = [i.lower() for i in valid_suffixes]
    return sorted(
        file for file in path.rglob("*")
        if file.suffix.lower() in valid_suffixes and
        (name_pattern is None or re.match(name_pattern, file.name))
    )


def snapshot(kcl_path: Path, save_path: Path) -> bool:
    try:
        snapshot_response = asyncio.run(
            kcl.execute_and_snapshot(str(kcl_path.parent), kcl.ImageFormat.Png)
        )

        image = Image.open(BytesIO(bytearray(snapshot_response)))

        im_path = save_path.with_suffix(".png")

        image.save(im_path)

        return True
    except Exception as e:
        print(e)
        return False


def update_step_file_dates(step_file_path: Path) -> None:
    # https://github.com/KittyCAD/cli/blob/main/src/cmd_kcl.rs#L1092
    regex = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+\+\d{2}:\d{2}"
    subst = r"1970-01-01T00:00:00.0+00:00"

    with open(step_file_path, "r") as inp:
        contents = inp.read()

    contents = re.sub(regex, subst, contents)

    with open(step_file_path, "w") as out:
        out.write(contents)


def process_single_kcl(kcl_path: Path) -> dict:
    # The part name is the parent folder since each file is main.kcl
    part_name = kcl_path.parent.name
    src_name = kcl_path.parts[-3]

    print(f"Processing {src_name}/{part_name}")

    # determine the root dir, which is where this python script
    root_dir = Path(__file__).parent
    # step and screenshots for the part are based on the root dir
    step_path = root_dir / "step" / part_name
    screenshots_path = root_dir / "screenshots" / part_name

    # attempt step export
    export_status = export_step(kcl_path=kcl_path, save_path=step_path)
    count = 1
    while not export_status and count < RETRIES:
        export_status = export_step( kcl_path=kcl_path, save_path=step_path)
        count += 1

    # attempt screenshot
    snapshot_status = snapshot(kcl_path=kcl_path, save_path=screenshots_path)
    count = 1
    while not snapshot_status and count < RETRIES:
        snapshot_status = snapshot(kcl_path=kcl_path, save_path=screenshots_path)
        count += 1

    # find relative paths, used for building the README.md
    kcl_rel_path = kcl_path.relative_to(Path(__file__).parent)

    return {"filename": f"{kcl_rel_path}", "export_status": export_status, "snapshot_status": snapshot_status}


def main():
    kcl_files = find_files(path=Path(__file__).parent, valid_suffixes=[".kcl"], name_pattern="main")

    # run concurrently
    with ProcessPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_kcl, kcl_file) for kcl_file in kcl_files]
    results = [future.result() for future in futures]

    step_files = find_files(path=Path(__file__).parent, valid_suffixes=[".step"])
    with ProcessPoolExecutor(max_workers=5) as executor:
        _ = [executor.submit(update_step_file_dates, step_file) for step_file in step_files]

    if False in [i["export_status"] for i in results]:
        comment_body = "The following files failed to export to STEP format:\n"
        for i in results:
            if not i["export_status"]:
                comment_body += f"{i['filename']}\n"

        url = f"https://api.github.com/repos/{os.getenv('GH_REPO')}/issues/{os.getenv('GH_PR')}/comments"

        headers = {
            'Authorization': f'token {os.getenv("GH_TOKEN")}',
        }

        json_data = {
            'body': comment_body,
        }

        requests.post(url, headers=headers, json=json_data, timeout=60)


if __name__ == "__main__":
    main()
