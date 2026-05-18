"""
Capture Azure Portal reference screenshots for ITL Control Plane Portal designers.

Existing screenshots 01-06 = current ITL Portal design.
This script produces 07-18 = Azure Portal design reference.

Run:
    python demo/capture-azure-ref.py

Requirements: playwright must be installed and Edge available.
The script reuses the local Edge profile so Azure Portal auth is already present.
"""

import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

DEMO_DIR = Path(__file__).parent
EDGE_USER_DATA = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Edge" / "User Data"

VIEWS = [
    ("07-azure-portal-home",           "https://portal.azure.com/#home"),
    ("08-azure-portal-all-resources",  "https://portal.azure.com/#view/HubsExtension/ServiceMenuBlade/extension/Microsoft_Azure_Resources/menuId/ResourceManager/itemId/browseAll"),
    ("09-azure-portal-resource-groups","https://portal.azure.com/#servicemenu/Microsoft_Azure_Resources/ResourceManager/resourcegroups"),
    ("10-azure-portal-virtual-machines","https://portal.azure.com/#blade/Microsoft_Azure_ComputeHub/ComputeHubMenuBlade/virtualMachinesBrowse"),
    ("11-azure-portal-create-hub",     "https://portal.azure.com/#create/hub"),
    ("12-azure-portal-dashboard",      "https://portal.azure.com/#dashboard"),
    ("13-azure-portal-settings-dir",   "https://portal.azure.com/#settings/directory"),
    ("14-azure-portal-settings-appearance", "https://portal.azure.com/#settings/appearance"),
    ("15-azure-portal-monitor",        "https://portal.azure.com/#blade/Microsoft_Azure_Monitoring/AzureMonitoringBrowseBlade"),
    ("16-azure-portal-entra-id",       "https://portal.azure.com/#blade/Microsoft_AAD_IAM/ActiveDirectoryMenuBlade"),
    ("17-azure-portal-storage-accounts","https://portal.azure.com/#view/Microsoft_Azure_StorageHub/StorageHub.MenuView/~/StorageAccountsBrowse"),
    ("18-azure-portal-notifications",  None),   # captured via bell click below
]


async def main() -> None:
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(EDGE_USER_DATA),
            channel="msedge",
            headless=False,
            viewport={"width": 1440, "height": 900},
            args=["--profile-directory=Default"],
        )

        page = context.pages[0] if context.pages else await context.new_page()

        for name, url in VIEWS:
            out = DEMO_DIR / f"{name}.png"
            if out.exists():
                print(f"  skip  {out.name}  (already exists)")
                continue

            if url is None:
                # Notifications panel: navigate home first then click bell
                await page.goto("https://portal.azure.com/#home", wait_until="load", timeout=60_000)
                await page.wait_for_timeout(3000)
                bell = page.get_by_role("button", name="Notifications")
                await bell.click()
                await page.wait_for_timeout(2000)
            else:
                await page.goto(url, wait_until="load", timeout=60_000)
                await page.wait_for_timeout(3500)

            await page.screenshot(path=str(out), full_page=False)
            print(f"  saved {out.name}")

        await context.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
