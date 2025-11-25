import subprocess
import time
import threading
import requests
import tkinter as tk
from tkinter import messagebox, font
from stem import Signal
from stem.control import Controller
import configparser
import os
import sys

# --- 主应用程序类 ---
class RotatorApp:
    def __init__(self, root_window):
        self.root = root_window
        self.config = configparser.ConfigParser()

        # --- 进程与状态变量 ---
        self.tor_process = None
        self.clash_process = None
        self.is_rotation_enabled = True
        self.interval_sec = 60
        self.rotator_thread = None

        # --- GUI变量 ---
        self.ip_var = tk.StringVar(value="当前 IP: 正在获取...")
        self.state_var = tk.StringVar(value="轮换状态: 已开启")
        self.interval_var = tk.StringVar()

        # --- 初始化 ---
        self._load_config()
        self._setup_ui()
        
        if self._validate_paths():
            self._initial_start()
        else:
            self.root.destroy() # 如果路径无效则退出
            sys.exit(1)

    def _load_config(self):
        """加载 config.ini 配置文件"""
        if not os.path.exists('config.ini'):
            messagebox.showerror("错误", "配置文件 'config.ini' 未找到！\n请确保它与脚本在同一目录下。")
            self.root.destroy()
            sys.exit(1)
        
        self.config.read('config.ini', encoding='utf-8')
        try:
            self.tor_path = self.config['Paths']['tor_executable']
            self.tor_rc = self.config['Paths']['tor_rc_file']
            self.clash_path = self.config['Paths']['clash_executable']
            self.control_port = self.config.getint('Settings', 'control_port')
            self.control_password = self.config['Settings']['control_password']
            self.clash_port = self.config.getint('Settings', 'clash_proxy_port')
            self.interval_sec = self.config.getint('Settings', 'default_interval_seconds')
            self.interval_var.set(str(self.interval_sec))
        except (KeyError, configparser.NoSectionError) as e:
            messagebox.showerror("配置错误", f"配置文件 'config.ini' 中缺少必要的键或区域: {e}")
            self.root.destroy()
            sys.exit(1)

    def _validate_paths(self):
        """验证配置文件中的路径是否存在"""
        if not os.path.exists(self.tor_path):
            messagebox.showerror("路径错误", f"Tor 可执行文件未找到:\n{self.tor_path}")
            return False
        if not os.path.exists(self.clash_path):
            messagebox.showerror("路径错误", f"Clash 可执行文件未找到:\n{self.clash_path}")
            return False
        return True

    def _setup_ui(self):
        """创建图形用户界面"""
        self.root.title("Tor + Clash 轮换器")
        self.root.geometry("480x220")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        default_font = font.nametofont("TkDefaultFont")
        default_font.configure(family="Microsoft YaHei", size=10)

        ip_label = tk.Label(self.root, textvariable=self.ip_var, font=("Segoe UI", 10))
        ip_label.pack(pady=10)

        self.state_label = tk.Label(self.root, textvariable=self.state_var, font=("Segoe UI", 11), fg="green")
        self.state_label.pack()

        frame = tk.Frame(self.root)
        frame.pack(pady=10)

        self.toggle_button = tk.Button(frame, text="开启/关闭轮换", width=16, command=self._toggle_rotation)
        self.toggle_button.grid(row=0, column=0, padx=6)
        
        tk.Label(frame, text="间隔 (秒):").grid(row=0, column=1, padx=6)
        tk.Entry(frame, width=6, textvariable=self.interval_var).grid(row=0, column=2)
        tk.Button(frame, text="应用", command=self._apply_interval).grid(row=0, column=3, padx=6)

        self.change_ip_button = tk.Button(self.root, text="立即更换 IP", command=self._manual_change_ip)
        self.change_ip_button.pack(pady=10)

    def _initial_start(self):
        """程序启动时的初始化操作"""
        self.start_tor()
        self.start_clash()
        # 在后台线程中启动轮换器
        self.rotator_thread = threading.Thread(target=self._rotator_thread_loop, daemon=True)
        self.rotator_thread.start()

    # --- 核心功能方法 ---

    def _change_ip_task(self):
        """更换 IP 的核心任务，适合在后台线程中运行"""
        self.root.after(0, lambda: self.ip_var.set("当前 IP: 正在更换..."))
        try:
            with Controller.from_port(port=self.control_port) as c:
                c.authenticate(password=self.control_password)
                c.signal(Signal.NEWNYM)
            # 等待一小段时间让新链路建立
            time.sleep(2)
            new_ip = self._get_ip_via_clash()
            self.root.after(0, lambda: self.ip_var.set("当前 IP: " + new_ip))
        except Exception as e:
            print(f"更换 IP 时出错: {e}")
            self.root.after(0, lambda: self.ip_var.set("错误: 更换 IP 失败"))
        finally:
            # 确保按钮在操作结束后恢复可用
            self.root.after(0, lambda: self.change_ip_button.config(state=tk.NORMAL))

    def _get_ip_via_clash(self):
        """通过 Clash 代理获取公网 IP"""
        proxies = {
            "http": f"http://127.0.0.1:{self.clash_port}",
            "https": f"http://127.0.0.1:{self.clash_port}"
        }
        try:
            r = requests.get("https://api.ipify.org", proxies=proxies, timeout=15)
            r.raise_for_status()
            return r.text.strip()
        except requests.exceptions.RequestException as e:
            print(f"获取 IP 时请求错误: {e}")
            return "网络错误"

    def _rotator_thread_loop(self):
        """后台循环线程，用于自动轮换 IP"""
        # 初始延迟，等待服务启动
        time.sleep(5)
        while True:
            if self.is_rotation_enabled:
                self._change_ip_task()
            
            # 等待指定的时间间隔
            start_time = time.time()
            while time.time() - start_time < self.interval_sec:
                time.sleep(1)
                # 如果在等待期间轮换被禁用，则立即中断等待
                if not self.is_rotation_enabled:
                    break
    
    # --- 进程管理 ---

    def _start_process(self, command, process_var_name):
        """通用启动进程函数"""
        process = getattr(self, process_var_name)
        if not process or process.poll() is not None:
            try:
                # CREATE_NO_WINDOW 标志让程序在后台运行，没有命令行窗口
                proc = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
                setattr(self, process_var_name, proc)
                print(f"{command[0]} 已启动。")
            except Exception as e:
                messagebox.showerror("错误", f"启动 {os.path.basename(command[0])} 失败: {e}")

    def _stop_process(self, process_var_name):
        """通用停止进程函数，带超时处理"""
        process = getattr(self, process_var_name)
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
                print(f"{process_var_name} 已终止。")
            except subprocess.TimeoutExpired:
                print(f"{process_var_name} 未能正常终止，正在强制结束。")
                process.kill()
            finally:
                setattr(self, process_var_name, None)

    def start_tor(self):
        self._start_process([self.tor_path, "-f", self.tor_rc], 'tor_process')

    def stop_tor(self):
        self._stop_process('tor_process')

    def start_clash(self):
        self._start_process([self.clash_path], 'clash_process')
        self._set_system_proxy(enable=True, port=self.clash_port)

    def stop_clash(self):
        self._stop_process('clash_process')
        self._set_system_proxy(enable=False)

    # --- 系统代理管理 ---
    def _set_system_proxy(self, enable=False, port=7890):
        """启用或禁用 Windows 系统代理"""
        try:
            if enable:
                subprocess.run([
                    "reg", "add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                    "/v", "ProxyEnable", "/t", "REG_DWORD", "/d", "1", "/f"
                ], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
                subprocess.run([
                    "reg", "add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                    "/v", "ProxyServer", "/t", "REG_SZ", "/d", f"127.0.0.1:{port}", "/f"
                ], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
                print(f"系统代理已开启 (端口: {port})")
            else:
                subprocess.run([
                    "reg", "add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                    "/v", "ProxyEnable", "/t", "REG_DWORD", "/d", "0", "/f"
                ], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
                print("系统代理已关闭。")
        except Exception as e:
            print(f"设置系统代理时出错: {e}")

    # --- GUI 事件处理 ---

    def _toggle_rotation(self):
        """切换自动轮换的开关"""
        self.is_rotation_enabled = not self.is_rotation_enabled
        if self.is_rotation_enabled:
            self.start_tor()
            self.start_clash()
            self.state_var.set("轮换状态: 已开启")
            self.state_label.config(fg="green")
        else:
            self.stop_tor()
            self.stop_clash()
            self.state_var.set("轮换状态: 已关闭")
            self.state_label.config(fg="red")

    def _apply_interval(self):
        """应用新的时间间隔"""
        try:
            v = int(self.interval_var.get())
            if 3 <= v <= 3600:
                self.interval_sec = v
                messagebox.showinfo("成功", f"时间间隔已设置为: {self.interval_sec} 秒。")
            else:
                messagebox.showwarning("输入错误", "间隔必须在 3 到 3600 秒之间。")
        except ValueError:
            messagebox.showwarning("输入错误", "请输入一个有效的整数。")

    def _manual_change_ip(self):
        """手动触发一次 IP 更换"""
        if self.is_rotation_enabled:
            self.change_ip_button.config(state=tk.DISABLED)
            threading.Thread(target=self._change_ip_task, daemon=True).start()
        else:
            messagebox.showwarning("提示", "请先开启轮换功能。")
    
    def _on_close(self):
        """处理窗口关闭事件，确保所有进程和代理都被清理"""
        if messagebox.askokcancel("退出", "确定要退出吗？\n所有相关进程将被关闭。"):
            self.is_rotation_enabled = False # 停止后台循环
            # 等待一小会儿，让循环检测到状态变化
            time.sleep(1.1)
            self.stop_tor()
            self.stop_clash()
            self.root.destroy()


# --- 程序入口 ---
if __name__ == "__main__":
    main_root = tk.Tk()
    app = RotatorApp(main_root)
    main_root.mainloop()
