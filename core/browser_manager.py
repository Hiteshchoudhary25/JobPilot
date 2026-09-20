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

    # URLs that confirm a successful login for each platform
    LOGGED_IN_INDICATORS = {
        'linkedin': ['linkedin.com/feed', 'linkedin.com/in/', 'linkedin.com/mynetwork', 'linkedin.com/jobs'],
        'naukri':   ['naukri.com/mnjuser', 'naukri.com/user', 'naukri.com/home'],
        'indeed':   ['indeed.com/account', 'indeed.com/myjobs'],
    }
    LOGIN_WALL_INDICATORS = {
        'linkedin': ['linkedin.com/login', 'authwall', 'uas/login'],
        'naukri':   ['nlogin'],
        'indeed':   ['secure.indeed.com/auth'],
    }

    def _is_logged_in(self, url: str, platform: str) -> bool:
        url_lower = url.lower()
        return any(ind in url_lower for ind in self.LOGGED_IN_INDICATORS.get(platform, []))

    def _is_login_wall(self, url: str, platform: str) -> bool:
        url_lower = url.lower()
        return any(ind in url_lower for ind in self.LOGIN_WALL_INDICATORS.get(platform, []))

    def interactive_login(self, platform: str):
        """Launches a visible browser window for the user to log in.
        Auto-closes if already logged in — no ENTER needed."""
        user_dir = self.get_session_dir(platform)
        feed_urls = {
            'linkedin': 'https://www.linkedin.com/feed/',
            'naukri':   'https://www.naukri.com/',
            'indeed':   'https://www.indeed.com/',
        }
        login_urls = {
            'linkedin': 'https://www.linkedin.com/login',
            'naukri':   'https://www.naukri.com/nlogin/login',
            'indeed':   'https://secure.indeed.com/auth',
        }
        # Try feed URL first to detect if already logged in
        start_url = feed_urls.get(platform, login_urls.get(platform, 'https://www.google.com'))

        console.print(f"[bold cyan]Checking {platform.upper()} session...[/bold cyan]")

        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=user_dir,
                    headless=False,
                    executable_path=chrome_path if os.path.exists(chrome_path) else None,
                    no_viewport=True,
                    ignore_default_args=["--enable-automation"],
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-infobars',
                        '--start-maximized',
                    ],
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )

                page = context.pages[0] if context.pages else context.new_page()

                try:
                    page.goto(start_url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(3000)
                except Exception as e:
                    console.print(f"[dim]Navigation note: {e}[/dim]")

                current_url = page.url

                # --- Already logged in? Close automatically ---
                if self._is_logged_in(current_url, platform):
                    console.print(f"[bold green]Already logged in to {platform.upper()}![/bold green]")
                    console.print(f"[green]Session is valid. No action needed.[/green]")
                    try:
                        context.close()
                    except Exception:
                        pass
                    console.print(f"[bold green]Session confirmed for {platform.upper()}![/bold green]")
                    return

                # --- Not logged in — ask user to login manually ---
                console.print(f"[bold yellow]Not logged in to {platform.upper()}.[/bold yellow]")
                console.print(f"[cyan]Complete your login in the browser window that just opened.[/cyan]")
                console.print(f"[cyan]Once you see your home feed/dashboard, press ENTER here.[/cyan]")

                # Navigate to login page if on wrong page
                if not self._is_login_wall(current_url, platform):
                    try:
                        page.goto(login_urls.get(platform, start_url), wait_until="domcontentloaded", timeout=10000)
                    except Exception:
                        pass

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

                # Final verification
                try:
                    final_url = page.url
                    if self._is_logged_in(final_url, platform):
                        console.print(f"[bold green]Session verified! Logged in at: {final_url[:70]}[/bold green]")
                    elif self._is_login_wall(final_url, platform):
                        console.print(f"[bold red]WARNING: Still on login page — session NOT saved properly.[/bold red]")
                        console.print(f"[yellow]Re-run login and complete all login steps before pressing ENTER.[/yellow]")
                    else:
                        console.print(f"[bold green]Session saved. Current page: {final_url[:70]}[/bold green]")
                except Exception:
                    pass

                try:
                    context.close()
                except Exception:
                    pass

            console.print(f"[bold green]Session saved for {platform.upper()}![/bold green]")

        except KeyboardInterrupt:
            console.print(f"\n[yellow]Exited by user (Ctrl+C). Session preserved for {platform.upper()}.[/yellow]")