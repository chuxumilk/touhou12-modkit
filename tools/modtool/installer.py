# -*- coding: utf-8 -*-
"""TH12 魔改工具 —— 安装器

用 PyInstaller 打包成单文件 Setup.exe，内部带着 app.zip（程序本体）。
安装过程：解压到目标目录 → 创建快捷方式 → 写卸载脚本 → 可选立即启动。
"""

import io
import os
import subprocess
import sys
import zipfile

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "TH12 魔改工具"
APP_EXE = "TH12ModTool.exe"
ZIP_NAME = "app.zip"
VERSION = "1.0"


def bundle_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def default_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "TH12ModTool")


def ps(cmd):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", cmd],
        capture_output=True, text=True)


def make_shortcut(lnk, target, workdir):
    ps('$s = (New-Object -ComObject WScript.Shell).CreateShortcut("%s");'
       '$s.TargetPath = "%s"; $s.WorkingDirectory = "%s";'
       '$s.Description = "%s"; $s.Save()'
       % (lnk, target, workdir, APP_NAME))


def start_menu_dir():
    return os.path.join(os.environ.get("APPDATA", ""),
                        "Microsoft", "Windows", "Start Menu", "Programs")


class Installer(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        self.title("%s 安装程序 v%s" % (APP_NAME, VERSION))
        self.resizable(False, False)
        self.configure(bg="#f4f5f7")
        try:
            self.iconbitmap(default="")
        except Exception:
            pass
        self._build()

    def _build(self):
        pad = {"padx": 14, "pady": 6}
        head = tk.Frame(self, bg="#2b2f3a")
        head.pack(fill="x")
        tk.Label(head, text="  %s" % APP_NAME, bg="#2b2f3a", fg="#ffffff",
                 font=("Microsoft YaHei", 15, "bold")).pack(side="left",
                                                            pady=14)
        tk.Label(head, text="v%s  " % VERSION, bg="#2b2f3a", fg="#9aa4b8",
                 font=("Microsoft YaHei", 10)).pack(side="right", pady=18)

        body = tk.Frame(self, bg="#f4f5f7")
        body.pack(fill="both", expand=True)

        tk.Label(body, text="安装位置：", bg="#f4f5f7",
                 font=("Microsoft YaHei", 10)).grid(row=0, column=0,
                                                    sticky="w", **pad)
        self.path_var = tk.StringVar(value=default_dir())
        entry = tk.Entry(body, textvariable=self.path_var, width=46,
                         font=("Consolas", 10))
        entry.grid(row=0, column=1, sticky="we", **pad)
        tk.Button(body, text="浏览…", command=self.browse,
                  width=8).grid(row=0, column=2, **pad)

        self.desk_var = tk.IntVar(value=1)
        self.start_var = tk.IntVar(value=1)
        self.launch_var = tk.IntVar(value=1)
        tk.Checkbutton(body, text="创建桌面快捷方式", variable=self.desk_var,
                       bg="#f4f5f7").grid(row=1, column=1, sticky="w")
        tk.Checkbutton(body, text="创建开始菜单快捷方式", variable=self.start_var,
                       bg="#f4f5f7").grid(row=2, column=1, sticky="w")
        tk.Checkbutton(body, text="安装完成后立即启动", variable=self.launch_var,
                       bg="#f4f5f7").grid(row=3, column=1, sticky="w")

        tk.Label(body, text="提示：工具本身不含游戏文件，第一次启动后请在"
                            "右上角「设置目录」里选择你的\n"
                            "《东方星莲船》游戏文件夹（里面有 th12.dat）。",
                 bg="#f4f5f7", fg="#5a6478", justify="left",
                 font=("Microsoft YaHei", 9)).grid(row=4, column=0,
                                                   columnspan=3, sticky="w",
                                                   padx=14, pady=(10, 2))
        self.status = tk.Label(body, text="准备就绪", bg="#f4f5f7",
                               fg="#2b6cb0", font=("Microsoft YaHei", 9))
        self.status.grid(row=5, column=0, columnspan=3, sticky="w", padx=14)
        self.bar = ttk.Progressbar(body, length=460, mode="determinate")
        self.bar.grid(row=6, column=0, columnspan=3, padx=14, pady=(4, 8))

        foot = tk.Frame(self, bg="#e8eaee")
        foot.pack(fill="x")
        self.btn = tk.Button(foot, text="开始安装", width=14,
                             command=self.do_install, bg="#c8102e", fg="white",
                             activebackground="#a00c24", activeforeground="white",
                             font=("Microsoft YaHei", 10, "bold"),
                             relief="flat", pady=6)
        self.btn.pack(side="right", padx=12, pady=10)
        tk.Button(foot, text="取消", width=10, command=self.destroy,
                  relief="flat", pady=6).pack(side="right", pady=10)

    def browse(self):
        d = filedialog.askdirectory(title="选择安装位置",
                                    initialdir=self.path_var.get())
        if d:
            self.path_var.set(os.path.join(d, "TH12ModTool"))

    def do_install(self):
        dest = self.path_var.get().strip()
        if not dest:
            messagebox.showwarning(APP_NAME, "请填写安装位置")
            return
        zip_path = bundle_path(ZIP_NAME)
        if not os.path.isfile(zip_path):
            messagebox.showerror(APP_NAME, "安装包损坏：缺少 %s" % ZIP_NAME)
            return
        self.btn.config(state="disabled")
        try:
            self._extract(zip_path, dest)
            exe = os.path.join(dest, APP_EXE)
            self._write_uninstaller(dest)
            self.status.config(text="正在创建快捷方式…")
            self.update()
            if self.desk_var.get():
                desk = os.path.join(os.path.expanduser("~"), "Desktop")
                if os.path.isdir(desk):
                    make_shortcut(os.path.join(desk, APP_NAME + ".lnk"),
                                  exe, dest)
            if self.start_var.get():
                sm = start_menu_dir()
                if os.path.isdir(sm):
                    make_shortcut(os.path.join(sm, APP_NAME + ".lnk"),
                                  exe, dest)
            self.bar["value"] = 100
            self.status.config(text="安装完成！")
            self.update()
            if messagebox.askyesno(
                    APP_NAME,
                    "安装完成！\n\n位置：%s\n\n是否现在启动？" % dest):
                subprocess.Popen([exe], cwd=dest)
            self.destroy()
        except Exception as ex:
            self.btn.config(state="normal")
            messagebox.showerror(APP_NAME, "安装失败：\n%s" % ex)

    def _extract(self, zip_path, dest):
        if not os.path.isdir(dest):
            os.makedirs(dest)
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            total = max(1, len(names))
            self.status.config(text="正在解压…")
            for i, name in enumerate(names):
                z.extract(name, dest)
                if i % 20 == 0:
                    self.bar["value"] = i * 100.0 / total
                    self.update()

    def _write_uninstaller(self, dest):
        bat = os.path.join(dest, "卸载.bat")
        with io.open(bat, "w", encoding="gbk") as f:
            f.write(
                "@echo off\r\n"
                "chcp 65001 >nul\r\n"
                "echo 正在卸载 %s ...\r\n"
                "echo 只会删除程序文件，不会动你的游戏。\r\n"
                "pause\r\n"
                "del /f /q \"%%USERPROFILE%%\\Desktop\\%s.lnk\" 2>nul\r\n"
                "del /f /q \"%%APPDATA%%\\Microsoft\\Windows\\Start Menu\\"
                "Programs\\%s.lnk\" 2>nul\r\n"
                "cd /d \"%%~dp0..\"\r\n"
                "rd /s /q \"%%~dp0\" 2>nul\r\n"
                "echo 已卸载。\r\n"
                "pause\r\n" % (APP_NAME, APP_NAME, APP_NAME))


def main():
    app = Installer()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
