#!/usr/bin/env python3

import time
import re
import sys
import argparse
import requests
from pathlib import Path
from typing import Optional
import pandas as pd
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
# from astropy.time import Time

# ======== Configuration =========
STATE_PATH = "ep_oauth_state.json"     # Reuse login state (storage_state is recommended)

PROTECTED_URL = "https://ep.bao.ac.cn/ep/data_center/fxt_obs/"
API_URL = "https://ep.bao.ac.cn/ep/data_center/fxt_obs/api/"

# PROTECTED_URL = "https://ep.bao.ac.cn/ep/data_center/wxt_observation_data"
# API_URL = "https://ep.bao.ac.cn/ep/data_center/api/wxt_observation_data/"


class OutputLogger:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download Einstein Probe FXT data."
    )

    parser.add_argument(
        "--mode",
        choices=["source", "obslist"],
        required=True,
        help="Search by source catalogue or use an existing observation list."
    )

    parser.add_argument("--catalog", help="Source catalogue CSV.")
    parser.add_argument("--obs-list", help="Existing observation-list CSV.")

    parser.add_argument(
        "--detnam",
        choices=["FXTA", "FXTB", "ALL"],
        default="ALL",
        help="Detector to process."
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("./fxt_data"),
        help="Output directory."
    )

    parser.add_argument(
        "--username",
        required=True,
        help="EP username."
    )

    parser.add_argument(
        "--visible",
        action="store_true",
        help="Show the browser window."
    )

    parser.add_argument(
    "--page-timeout",
    type=int,
    default=300,
    metavar="SECONDS",
    help="Maximum time allowed for one page operation (default: 300 s)."
    )

    parser.add_argument(
        "--async-timeout",
        type=int,
        default=300,
        metavar="SECONDS",
        help="Maximum wait time for asynchronous packaging (default: 300 s)."
    )

    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        metavar="N",
        help="Number of retries after a failed observation (default: 2)."
    )

    args = parser.parse_args()

    if args.mode == "source" and not args.catalog:
        parser.error("--catalog is required when --mode=source")

    if args.mode == "obslist" and not args.obs_list:
        parser.error("--obs-list is required when --mode=list")

    args.output.mkdir(parents=True, exist_ok=True)

    return args


def mkrow_dir(destination_path: Path, obs_id: str, detnam: str, version: str) -> Path:
    d = destination_path / f"{obs_id}_{detnam}_{version}"
    d.mkdir(parents=True, exist_ok=True)
    return d

def wait_async_link(page) -> Optional[str]:
    """
    After clicking "Download User data", wait for the page to write the download link to the href of #async-download-link a.
    Return href (download_url), or None on timeout.
    """
    # Click the start button; in the source, it is <button ... onclick="startAsyncDownload(...)" >
    page.get_by_role("button", name="Download User data").click()
    page.wait_for_selector("#async-download-status", state="visible", timeout=30_000)

    # The page polls automatically every 3 seconds; we also set a maximum wait time
    t0 = time.time()
    while time.time() - t0 < ASYNC_MAX_WAIT:
        try:
            # When the download is ready, the container is visible and the a tag has an href
            page.wait_for_function(
                """() => {
                    const box = document.querySelector('#async-download-link');
                    const a = box ? box.querySelector('a') : null;
                    return box && getComputedStyle(box).display !== 'none' && a && a.href;
                }""",
                timeout=5_000
            )
            href = page.eval_on_selector("#async-download-link a", "el => el.href")
            return href
        except PWTimeout:
            # Also read the status text to help troubleshoot using the logs
            try:
                status = page.inner_text("#async-status-text")
                print("  [async] status =", status)
            except Exception:
                pass
            continue
    return None

def download_user_zip(page, out_dir: Path, obs_id: str, detnam: str, version: str) -> Path:
    """Package asynchronously and download the zip; return the saved path."""
    target = out_dir / f"{obs_id}_{detnam}_{version}_user.zip"
    if target.exists():
        print(f"  [skip] user zip exists: {target.name}")
        return target

    # Wait for the download link to appear
    href = wait_async_link(page)
    if not href:
        raise RuntimeError("Async user-data link did not appear within time limit.")

    # Download (the a tag has target=_blank; capture with expect_download)
    with page.expect_download(timeout=PER_PAGE_TIMEOUT) as dl_info:
        page.click("#async-download-link a")
    dl = dl_info.value
    dl.save_as(target.as_posix())
    print(f"  [ok] user zip -> {target.name}")
    return target

def download_sources_csv(page, out_dir: Path, obs_id: str, detnam: str, version: str) -> Path:
    """Click Export CSV to download the table and save it with a custom filename."""
    target = out_dir / f"{obs_id}_{detnam}_{version}_sources.csv"
    if target.exists():
        print(f"  [skip] sources csv exists: {target.name}")
        return target

    # The button id is fixed as download_src
    page.wait_for_selector("#download_src", timeout=30_000)
    with page.expect_download(timeout=PER_PAGE_TIMEOUT) as dl_info:
        page.click("#download_src")
    dl = dl_info.value
    dl.save_as(target.as_posix())
    print(f"  [ok] sources csv -> {target.name}")
    return target

def process_one_row(page, row: pd.Series, destination_path: Path):
    """Process one row: open the detail page and download the user-data zip + source-table CSV."""
    obs_id = str(row["obs_id"]).strip()
    detnam = str(row["detnam"]).strip()
    version = str(row.get("version", "01")).strip()  # Use the version column directly if it exists in your df

    url = f"https://ep.bao.ac.cn/ep/data_center/fxt_obs_detail/{obs_id}/{detnam}/{version}"
    print(f"\n==> {obs_id} {detnam} {version}")
    page.goto(url, wait_until="domcontentloaded", timeout=PER_PAGE_TIMEOUT)

    # Confirm that the page has loaded the expected sections ("Get Data Files" and "Sources in the observation")
    page.wait_for_selector("text=Get Data Files", timeout=30_000)
    page.wait_for_selector("text=Sources in the observation", timeout=30_000)

    out_dir = mkrow_dir(destination_path, obs_id, detnam, version)

    # 1) Download User data (asynchronous packaging + download)
    try:
        download_user_zip(page, out_dir, obs_id, detnam, version)
    except Exception as e:
        print(f"  [err] user zip failed: {e}")

    # 2) Export CSV(source table)
    try:
        download_sources_csv(page, out_dir, obs_id, detnam, version)
    except Exception as e:
        print(f"  [err] sources csv failed: {e}")

def run_batch(df: pd.DataFrame, destination_path: Path):
    df_sorted = df

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.visible)
        ctx = browser.new_context(
            storage_state=STATE_PATH,
            accept_downloads=True
        )
        page = ctx.new_page()

        for i, row in df_sorted.iterrows():
            for attempt in range(1, RETRY_TIMES + 2):
                try:
                    process_one_row(page, row, destination_path)
                    break
                except Exception as e:
                    print(f"  [err] row {i} attempt {attempt} failed: {e}")
                    if attempt <= RETRY_TIMES:
                        time.sleep(3)
                        continue
                    else:
                        print("  [give up] move on.")
                        break

        browser.close()


def login_with_playwright(username, password, headless=False, state_path=STATE_PATH):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()
        ctx.clear_cookies()

        # 1) Start from the protected page (reuse the session if already logged in)
        page.goto(PROTECTED_URL, wait_until="domcontentloaded")

        # time.sleep(10)

        if "oauth.china-vo.org" not in page.url:
            ctx.storage_state(path=state_path)
            cookies = ctx.cookies()
            browser.close()
            s = requests.Session()
            for c in cookies:
                s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
            return s

        # 2) Enter the username and password on the OAuth page (a CAPTCHA may appear)
        page.wait_for_selector('input[name="username"]', timeout=60_000)
        page.fill('input[name="username"]', username)
        page.fill('input[name="password"]', password)
        if page.is_visible('input[name="captcha"]'):
            page.screenshot(path="login_need_captcha.png")
            code = input("Captcha required. Please check login_need_captcha.png and enter the code: ").strip()
            page.fill('input[name="captcha"]', code)

        # 3) Click login
        page.get_by_role("button", name=re.compile("login", re.I)).click()

        # 4) Wait for the URL to return to the ep domain (compatible with all versions)
        try:
            page.wait_for_url(re.compile(r"^https://ep\.bao\.ac\.cn/.*"), timeout=60_000)
        except PWTimeout:
            # Fallback: wait for an element indicating a logged-in state
            try:
                page.wait_for_selector('a[href="/ep/user/logout"], img.avatar-xs, :text("My Home")',
                                       timeout=60_000)
            except PWTimeout:
                raise RuntimeError(f"Login may have failed. Current URL: {page.url}")

        # 5) Save login state & export cookies
        ctx.storage_state(path=state_path)
        cookies = ctx.cookies()
        browser.close()

    s = requests.Session()
    for c in cookies:
        s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    return s


def main():
    global PER_PAGE_TIMEOUT, ASYNC_MAX_WAIT, RETRY_TIMES
    args = parse_args()
    log_file = open(
        args.output / f"fxt_download_{datetime.now():%Y%m%d_%H%M%S}.log",
        "a",
        buffering=1
    )
    sys.stdout = OutputLogger(sys.__stdout__, log_file)
    sys.stderr = OutputLogger(sys.__stderr__, log_file)

    PER_PAGE_TIMEOUT = args.page_timeout * 1000      # Maximum 5 minutes per page operation
    ASYNC_MAX_WAIT = args.async_timeout              # Wait up to 5 minutes for asynchronous packaging (in seconds)
    RETRY_TIMES = args.retries                        # Number of retries per row on error

    session = login_with_playwright(
        args.username,
        '123',
        headless=False
    )

    if args.mode == "source":

        source_df = pd.read_csv(args.catalog, index_col=0, dtype=str)
        for src_name1,row in source_df.iterrows():
            src_name = src_name1.replace(' ','')
            src_path = args.output / src_name
            src_path.mkdir(parents=True, exist_ok=True)

            params = {
                "obs_id": "",  # Enter a specific ID or leave blank
                "start_datetime": row['Star Time'], 
                "end_datetime": row['End Time'],
                "ra": row['RA'],
                "dec": row['DEC'],
                "radius": row['Radius'],
            }
            response = session.post(API_URL, data=params)

            print('\n------------- searching for ' + src_name1 + ' FXT data -------------')

            if response.status_code == 200:
                json_data = response.json()
            else:
                print("Request failed with status code:", response.status_code)

            response.raise_for_status()
            data = response.json()  # A list in which each element is one row of records

            df = pd.DataFrame(data)
            if not df.empty:
                df = df.sort_values(["obs_start"], ascending=[True]).reset_index(drop=True)
                df.to_csv(src_path/"fxt_obs_list.csv", index=False, encoding="utf-8-sig")
                if args.detnam != "ALL":
                    df = df[
                        df["detnam"].astype(str).str.strip() == args.detnam
                    ]
                run_batch(df, args.output)
        
    elif args.mode == 'obslist':
        df = pd.read_csv(args.obs_list, dtype=str)
        run_batch(df, args.output)

if __name__ == '__main__':
    main()
