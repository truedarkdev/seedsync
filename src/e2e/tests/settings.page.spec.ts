import {SettingsPage} from "./settings.page";

describe('Testing settings page', () => {
    let page: SettingsPage;

    beforeEach(async () => {
        page = new SettingsPage();
        await page.navigateTo();
    });

    it('should have right top title', () => {
        expect(page.getTopTitle()).toEqual("Settings");
    });
});
