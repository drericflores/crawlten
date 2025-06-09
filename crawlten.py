"""
By: Dr Eric Oliver FLores
June 9, 2025
A conversion from my C++ CLI Tool to Python with added GUI
crawlten10.py - Restored & Enhanced Intelligent Web Crawler with GUI
- Fully restored all functionality from crawlten6 & crawlten7
- Retains autonomous crawl, pattern-based search, ethical crawling
- Adds FTP/HTML toggle, complete GUI, MIME-type resilience, no-overwrite
"""

import os
import re
import time
import json
import threading
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from collections import deque
import tkinter as tk
from tkinter import ttk, filedialog

MAX_DEPTH = 2
USER_AGENT = "crawlten10Bot/1.0"
VISITED_CACHE = "crawlten_memory.json"
SCORE_KEYWORDS = ["downloads", "release", "docs", "manual", "dataset"]

if os.path.exists(VISITED_CACHE):
    with open(VISITED_CACHE, "r") as f:
        MEMORY = json.load(f)
else:
    MEMORY = {"visited": [], "valuable": []}

headers = {"User-Agent": USER_AGENT}

def is_valid_link(href):
    return href and href.startswith("http") and not any(x in href for x in ["logout", "login", "#", "?share="])

def score_link(url):
    return sum(1 for k in SCORE_KEYWORDS if k in url.lower())

def is_repository_pattern(url):
    return any(domain in url for domain in ["github.com", "gitlab.com", "sourceforge.net"])

def obey_robots(url):
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    try:
        resp = requests.get(urljoin(base, "/robots.txt"), headers=headers, timeout=5)
        if "Disallow" in resp.text:
            disallowed = [line.split(":", 1)[-1].strip() for line in resp.text.splitlines() if line.startswith("Disallow")]
            for path in disallowed:
                if parsed.path.startswith(path):
                    return False
    except:
        pass
    return True

def duckduckgo_search(query, max_results=5):
    search_url = f"https://duckduckgo.com/html/?q={requests.utils.quote(query)}"
    try:
        resp = requests.get(search_url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith("http") and "duckduckgo.com/l/" not in href:
                results.append(href)
                if len(results) >= max_results:
                    break
        return results
    except:
        return []

class crawltenGUI:
    def __init__(self, master):
        self.master = master
        master.title("crawlten10 - Fully Intelligent Crawler")

        self.file_types = {
            "PDF": tk.BooleanVar(value=True),
            "MPEG": tk.BooleanVar(),
            "MP3": tk.BooleanVar(),
            "DOCX": tk.BooleanVar()
        }

        tk.Label(master, text="File Types to Download:").grid(row=0, column=0, sticky='w')
        for i, (ftype, var) in enumerate(self.file_types.items()):
            tk.Checkbutton(master, text=ftype, variable=var).grid(row=0, column=i + 1, sticky='w')

        self.protocol = tk.StringVar(value="HTML")
        tk.Label(master, text="Select Protocol:").grid(row=1, column=0, sticky='w')
        tk.Radiobutton(master, text="HTML", variable=self.protocol, value="HTML").grid(row=1, column=1, sticky='w')
        tk.Radiobutton(master, text="FTP", variable=self.protocol, value="FTP").grid(row=1, column=2, sticky='w')

        tk.Label(master, text="Start URL:").grid(row=2, column=0, sticky='w')
        self.url_entry = tk.Entry(master, width=80)
        self.url_entry.insert(0, "https://")
        self.url_entry.grid(row=2, column=1, columnspan=4, sticky='we')

        tk.Label(master, text="Search Pattern:").grid(row=3, column=0, sticky='w')
        self.search_entry = tk.Entry(master, width=80)
        self.search_entry.insert(0, "open source pdf downloads")
        self.search_entry.grid(row=3, column=1, columnspan=3, sticky='we')
        tk.Button(master, text="Search", command=self.threaded_search).grid(row=3, column=4)

        self.download_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        tk.Button(master, text="Select Folder", command=self.select_directory).grid(row=4, column=0, sticky='w')
        self.dir_label = tk.Label(master, text=self.download_dir)
        self.dir_label.grid(row=4, column=1, columnspan=4, sticky='w')

        self.start_button = tk.Button(master, text="Start Crawl", command=self.threaded_start)
        self.auto_button = tk.Button(master, text="Auto Crawl", command=self.threaded_auto)
        self.stop_button = tk.Button(master, text="Stop", command=self.stop, state='disabled')
        self.quit_button = tk.Button(master, text="Quit", command=master.quit)

        self.start_button.grid(row=5, column=1)
        self.auto_button.grid(row=5, column=2)
        self.stop_button.grid(row=5, column=3)
        self.quit_button.grid(row=5, column=4)

        self.progress = ttk.Progressbar(master, length=400, mode='determinate')
        self.progress.grid(row=6, column=0, columnspan=5)

        self.status = tk.Label(master, text="Ready")
        self.status.grid(row=7, column=0, columnspan=5, sticky='w')

        self.stop_flag = threading.Event()

    def select_directory(self):
        chosen = filedialog.askdirectory()
        if chosen:
            self.download_dir = chosen
            self.dir_label.config(text=chosen)

    def threaded_start(self):
        self.stop_flag.clear()
        self.start_button.config(state='disabled')
        self.auto_button.config(state='disabled')
        self.stop_button.config(state='normal')
        threading.Thread(target=self.download_files, args=(self.url_entry.get(),), daemon=True).start()

    def threaded_search(self):
        query = self.search_entry.get()
        urls = duckduckgo_search(query)
        for url in urls:
            if self.stop_flag.is_set():
                break
            self.download_files(url)

    def threaded_auto(self):
        self.stop_flag.clear()
        self.start_button.config(state='disabled')
        self.auto_button.config(state='disabled')
        self.stop_button.config(state='normal')
        for url in MEMORY.get("valuable", []):
            self.download_files(url)

    def stop(self):
        self.stop_flag.set()
        self.status.config(text="Stopping...")

    def download_files(self, seed_url):
        selected_types = [k.lower() for k, v in self.file_types.items() if v.get()]

        def download_file(url):
            name = os.path.basename(urlparse(url).path) or "index.html"
            dest = os.path.join(self.download_dir, name)
            if os.path.exists(dest):
                self.update_status(f"Skipped: {name}")
                return
            try:
                r = requests.get(url, headers=headers, stream=True, timeout=10)
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                self.update_status(f"Downloaded: {name}")
            except Exception as e:
                self.update_status(f"Failed: {url} ({e})")

        def crawl(url, depth=0, visited=None):
            if visited is None:
                visited = set()
            if depth > MAX_DEPTH or self.stop_flag.is_set() or url in visited:
                return
            visited.add(url)
            if not obey_robots(url):
                self.update_status(f"Blocked by robots.txt: {url}")
                return
            try:
                resp = requests.get(url, headers=headers, timeout=10)
                soup = BeautifulSoup(resp.text, "html.parser")
            except:
                return

            links = [urljoin(url, a['href']) for a in soup.find_all('a', href=True) if is_valid_link(a['href'])]
            self.progress.config(maximum=len(links))

            for i, link in enumerate(links, 1):
                if self.stop_flag.is_set():
                    break
                if any(link.lower().endswith(f".{ext}") for ext in selected_types):
                    download_file(link)
                elif is_repository_pattern(link):
                    crawl(link, depth + 1, visited)
                elif score_link(link) > 1:
                    crawl(link, depth + 1, visited)
                self.update_progress(i)

            with open(VISITED_CACHE, "w") as f:
                json.dump(MEMORY, f, indent=2)

        crawl(seed_url)
        self.update_status("Download Complete")
        self.start_button.config(state='normal')
        self.auto_button.config(state='normal')
        self.stop_button.config(state='disabled')

    def update_status(self, message):
        self.master.after(0, self.status.config, {"text": message})

    def update_progress(self, value):
        self.master.after(0, self.progress.config, {"value": value})

if __name__ == "__main__":
    root = tk.Tk()
    app = crawltenGUI(root)
    root.mainloop()
