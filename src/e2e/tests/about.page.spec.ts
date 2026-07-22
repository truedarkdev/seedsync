import {AboutPage} from "./about.page";

describe('Testing about page', () => {
    let page: AboutPage;

    beforeEach(async () => {
        page = new AboutPage();
        await page.navigateTo();
    });

    it('should have right top title', async () => {
        expect(await page.getTopTitle()).toEqual("About");
    });

    it('should have the right version', async () => {
        expect(await page.getVersion()).toEqual("v0.9.0");
    });
});
