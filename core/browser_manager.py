import os
import time
import threading
from typing import Optional
from playwright.sync_api import sync_playwright
from rich.console import Console

console = Console()

class BrowserSessionManager:
    def __init__(self, base_session_dir: str = './browser_sessions'):
        self.base_session_dir = base_session_dir
        os.makedirs(base_session_dir, exist_ok=True)

    def get_session_dir(self, platform: str) -> str:
        path = os.path.join(self.base_session_dir, platform)
        os.makedirs(path, exist_ok=True)
        return path

    def interactive_login(self, platform: str):
        """Launches a visible browser window for the user to log in once."""
        user_dir = self.get_session_dir(platform)
        urls = {
            'linkedin': 'https://www.linkedin.com/login',
            'naukri': 'https://www.naukri.com/nlogin/login',
            'indeed': 'https://secure.indeed.com/auth'
        }
        target_url = urls.get(platform, 'https://www.google.com')

        console.print(f"[bold green]🚀 Launching interactive browser for {platform.upper()}...[/bold green]")
        console.print("[cyan]1. If not logged in, complete your login/OTP in the browser window.[/cyan]")
        console.print("[yellow]2. If already logged in (or when done), simply press [ENTER] in this terminal or close the browser window (or press Ctrl+C).[/yellow]")

        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=user_dir,
                    headless=False,
                    executable_path=chrome_path if os.path.exists(chrome_path) else None,
                    viewport={'width': 1280, 'height': 800},
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-infobars'
                    ]
                )

                page = context.pages[0] if context.pages else context.new_page()
                
                try:
                    page.goto(target_url)
                except Exception as e:
                    console.print(f"[dim]Navigation note: {e}[/dim]")

                closed_event = threading.Event()

                context.on("close", lambda _: closed_event.set())
                page.on("close", lambda _: closed_event.set())

                def wait_for_user_input():
                    try:
                        input()
                    except Exception:
                        pass
                    closed_event.set()

                input_thread = threading.Thread(target=wait_for_user_input, daemon=True)
                input_thread.start()

                while not closed_event.is_set():
                    time.sleep(0.3)
                    try:
                        if not context.pages or page.is_closed():
                            closed_event.set()
                            break
                    except Exception:
                        closed_event.set()
                        break

                try:
                    context.close()
                except Exception:
                    pass

            console.print(f"[bold green]✅ Session successfully saved for {platform.upper()}![/bold green]")

        except KeyboardInterrupt:
            console.print(f"\n[yellow]🚪 Exited by user (Ctrl+C). Session preserved for {platform.upper()}.[/yellow]")