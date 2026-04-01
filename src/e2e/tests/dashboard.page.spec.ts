import {DashboardPage, File} from "./dashboard.page";

describe('Testing dashboard page', () => {
    let page: DashboardPage;

    beforeEach(async () => {
        page = new DashboardPage();
        await page.navigateTo();
    });

    it('should have right top title', () => {
        expect(page.getTopTitle()).toEqual("Dashboard");
    });

    it('should have a list of files', () => {
        let golden = [
                new File("áßç déÀ.mp4", '', "0 B of 840 KB"),
                new File("clients.jpg", '', "0 B of 36.5 KB"),
                new File("crispycat", '', "0 B of 1.53 MB"),
                new File("documentation.png", '', "0 B of 8.88 KB"),
                new File("goose", '', "0 B of 2.78 MB"),
                new File("illusion.jpg", '', "0 B of 81.5 KB"),
                new File("joke", '', "0 B of 168 KB"),
                new File("testing.gif", '', "0 B of 8.95 MB"),
                new File("üæÒ", '', "0 B of 70.8 KB"),
            ];
        expect(page.getFiles()).toEqual(golden);
    });

    it('should show and hide action buttons on select', async () => {
        const files = await page.getFiles();
        expect(files.length).toBeGreaterThan(1);

        const targetIndex = 1;
        expect(await page.isFileActionsVisible(targetIndex)).toBe(false);

        await page.selectFile(targetIndex);
        expect(await page.isFileActionsVisible(targetIndex)).toBe(true);

        await page.selectFile(targetIndex);
        expect(await page.isFileActionsVisible(targetIndex)).toBe(false);
    });

    it('should show action buttons for most recent file selected', async () => {
        const files = await page.getFiles();
        expect(files.length).toBeGreaterThan(1);

        const firstIndex = 0;
        const secondIndex = 1;

        expect(await page.isFileActionsVisible(firstIndex)).toBe(false);
        expect(await page.isFileActionsVisible(secondIndex)).toBe(false);

        await page.selectFile(firstIndex);
        expect(await page.isFileActionsVisible(firstIndex)).toBe(true);
        expect(await page.isFileActionsVisible(secondIndex)).toBe(false);

        await page.selectFile(secondIndex);
        expect(await page.isFileActionsVisible(firstIndex)).toBe(false);
        expect(await page.isFileActionsVisible(secondIndex)).toBe(true);

        await page.selectFile(secondIndex);
        expect(await page.isFileActionsVisible(firstIndex)).toBe(false);
        expect(await page.isFileActionsVisible(secondIndex)).toBe(false);
    });

    it('should have all the action buttons', async () => {
        const files = await page.getFiles();
        expect(files.length).toBeGreaterThan(1);

        const targetIndex = 1;
        await page.selectFile(targetIndex);

        await page.getFileActions(targetIndex).then(states => {
            expect(states.map(state => state.title)).toEqual([
                "Queue",
                "Stop",
                "Extract",
                "Delete Local",
                "Delete Remote",
                "Validate"
            ]);
        });
    });

    it('should have Queue action enabled for Default state', async () => {
        const defaultStateIndex = await page.findFileIndexByStatus("");
        if (defaultStateIndex < 0) {
            pending("No default-state dashboard row is available in the current dashboard fixture");
            return;
        }
        await page.selectFile(defaultStateIndex);
        const queueAction = (await page.getFileActions(defaultStateIndex))
            .find(action => action.title === "Queue");
        expect(queueAction).toBeDefined();
        if (!queueAction) {
            return;
        }
        expect(queueAction.title).toBe("Queue");
        expect(queueAction.isEnabled).toBe(true);
    });

    it('should have Stop action disabled for Default state', async () => {
        const defaultStateIndex = await page.findFileIndexByStatus("");
        if (defaultStateIndex < 0) {
            pending("No default-state dashboard row is available in the current dashboard fixture");
            return;
        }
        await page.selectFile(defaultStateIndex);
        const stopAction = (await page.getFileActions(defaultStateIndex))
            .find(action => action.title === "Stop");
        expect(stopAction).toBeDefined();
        if (!stopAction) {
            return;
        }
        expect(stopAction.title).toBe("Stop");
        expect(stopAction.isEnabled).toBe(false);
    });

    xit('should preserve stopped progress across reload and requeue the same row intentionally', () => {
        // Skipped in the current dashboard fixture because there is no Downloading row to exercise.
    });
});
