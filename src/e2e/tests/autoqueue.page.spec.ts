import {AutoQueuePage} from "./autoqueue.page";

describe('Testing autoqueue page', () => {
    let page: AutoQueuePage;

    beforeEach(async () => {
        page = new AutoQueuePage();
        await page.navigateTo();
    });

    it('should have right top title', async () => {
        expect(await page.getTopTitle()).toEqual("Settings");
    });


    it('should add and remove patterns', async () => {
        // start with an empty list
        expect(await page.getPatterns()).toEqual([]);

        // add some patterns, and expect them in added order
        await page.addPattern("APattern");
        await page.addPattern("CPattern");
        await page.addPattern("DPattern");
        await page.addPattern("BPattern");
        expect(await page.getPatterns()).toEqual([
            "APattern", "CPattern", "DPattern", "BPattern"
        ]);

        // remove patterns one by one
        await page.removePattern(2);
        expect(await page.getPatterns()).toEqual([
            "APattern", "CPattern", "BPattern"
        ]);
        await page.removePattern(0);
        expect(await page.getPatterns()).toEqual([
            "CPattern", "BPattern"
        ]);
        await page.removePattern(1);
        expect(await page.getPatterns()).toEqual([
            "CPattern"
        ]);
        await page.removePattern(0);
        expect(await page.getPatterns()).toEqual([]);
    });

    it('should list existing patterns in alphabetical order', async () => {
        // start with an empty list
        expect(await page.getPatterns()).toEqual([]);

        // add some patterns, and expect them in added order
        await page.addPattern("APattern");
        await page.addPattern("CPattern");
        await page.addPattern("DPattern");
        await page.addPattern("BPattern");

        // reload the page
        await page.navigateTo();

        // patterns should be in alphabetical order
        expect(await page.getPatterns()).toEqual([
            "APattern", "BPattern", "CPattern", "DPattern"
        ]);

        // remove all patterns
        await page.removePattern(0);
        await page.removePattern(0);
        await page.removePattern(0);
        await page.removePattern(0);
        expect(await page.getPatterns()).toEqual([]);
    });
});
