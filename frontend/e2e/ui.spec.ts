import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const themes = ["light", "dark"] as const;
const widths = [375, 1280] as const;

const open = async (page: Page, theme: (typeof themes)[number], width: number) => {
  await page.addInitScript((t) => localStorage.setItem("fam-theme", t), theme);
  await page.setViewportSize({ width, height: 800 });
  await page.goto("/");
  await expect(page.getByLabel("Message to Skipper")).toBeVisible();
};

const axe = (page: Page) => new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();

for (const theme of themes) {
  for (const width of widths) {
    test(`axe clean: chat and folders dialog, ${theme} ${width}px`, async ({ page }) => {
      await open(page, theme, width);
      expect((await axe(page)).violations).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.getByRole("button", { name: "Folders & access" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      expect((await axe(page)).violations).toEqual([]);
    });
  }
}

test("send button is a 44px target and stays named", async ({ page }) => {
  await open(page, "light", 375);
  const box = await page.getByRole("button", { name: "Send" }).boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);
});

test("keyboard: Enter sends and focus returns to the textarea", async ({ page }) => {
  await open(page, "light", 1280);
  const input = page.getByLabel("Message to Skipper");
  await input.fill("hello crew");
  await input.press("Enter");
  await expect(input).toBeFocused();
});

test("folders dialog keeps focus inside, Esc closes and restores the opener", async ({ page }) => {
  await open(page, "dark", 1280);
  const opener = page.getByRole("button", { name: "Folders & access" });
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  for (let i = 0; i < 20; i++) await page.keyboard.press("Tab");
  expect(await dialog.evaluate((d) => d.contains(document.activeElement))).toBe(true);
  for (let i = 0; i < 20; i++) await page.keyboard.press("Shift+Tab");
  expect(await dialog.evaluate((d) => d.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});

test("theme toggle keeps a constant name and carries state in aria-pressed", async ({ page }) => {
  await open(page, "light", 1280);
  const toggle = page.getByRole("button", { name: "Dark mode" });
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});
