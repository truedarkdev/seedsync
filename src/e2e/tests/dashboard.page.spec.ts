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

    it('should show and hide action buttons on select', () => {
        expect(page.isFileActionsVisible(1)).toBe(false);
        page.selectFile(1);
        expect(page.isFileActionsVisible(1)).toBe(true);
        page.selectFile(1);
        expect(page.isFileActionsVisible(1)).toBe(false);
    });

    it('should show action buttons for most recent file selected', () => {
        expect(page.isFileActionsVisible(1)).toBe(false);
        expect(page.isFileActionsVisible(2)).toBe(false);
        page.selectFile(1);
        expect(page.isFileActionsVisible(1)).toBe(true);
        expect(page.isFileActionsVisible(2)).toBe(false);
        page.selectFile(2);
        expect(page.isFileActionsVisible(1)).toBe(false);
        expect(page.isFileActionsVisible(2)).toBe(true);
        page.selectFile(2);
        expect(page.isFileActionsVisible(1)).toBe(false);
        expect(page.isFileActionsVisible(2)).toBe(false);
    });

    it('should have all the action buttons', async () => {
        await page.getFileActions(1).then(states => {
            expect(states.map(state => state.title)).toEqual([
                "Queue",
                "Stop",
                "Extract",
                "Delete Local",
                "Delete Remote"
            ]);
        });
    });

    it('should have Queue action enabled for Default state', async () => {
        await page.getFiles().then(files => {
            expect(files[1].status).toEqual("");
        });
        const fileId = await page.getFileIdByIndex(1);
        const queueAction = await page.getFileActionByTitle(fileId, "Queue");
        expect(queueAction.title).toBe("Queue");
        expect(queueAction.isEnabled).toBe(true);
    });

    it('should have Stop action disabled for Default state', async () => {
        await page.getFiles().then(files => {
            expect(files[1].status).toEqual("");
        });
        const fileId = await page.getFileIdByIndex(1);
        const stopAction = await page.getFileActionByTitle(fileId, "Stop");
        expect(stopAction.title).toBe("Stop");
        expect(stopAction.isEnabled).toBe(false);
    });

    it('should preserve stopped progress across reload and requeue the same row intentionally', async () => {
        const downloadingIndex = await page.findFileIndexByStatus("Downloading");
        expect(downloadingIndex).toBeGreaterThan(-1);
        if (downloadingIndex < 0) {
            return;
        }

        const fileId = await page.getFileIdByIndex(downloadingIndex);
        const initialProgress = await page.getFileProgressById(fileId);
        const initialSpeed = await page.getFileSpeedById(fileId);
        const initialEta = await page.getFileEtaById(fileId);

        expect(initialSpeed).not.toEqual("");
        expect(initialEta).not.toEqual("");

        await page.selectFileById(fileId);
        await page.stopFileById(fileId);
        await page.waitForFileStatusById(fileId, "Stopped");

        const stoppedProgress = await page.getFileProgressById(fileId);
        expect(stoppedProgress).toBeGreaterThanOrEqual(initialProgress);
        expect(await page.getFileSpeedById(fileId)).toEqual("");
        expect(await page.getFileEtaById(fileId)).toEqual("");

        const stoppedQueueAction = await page.getFileActionByTitle(fileId, "Queue");
        const stoppedStopAction = await page.getFileActionByTitle(fileId, "Stop");
        expect(stoppedQueueAction.title).toBe("Queue");
        expect(stoppedQueueAction.isEnabled).toBe(true);
        expect(stoppedStopAction.title).toBe("Stop");
        expect(stoppedStopAction.isEnabled).toBe(false);

        await page.reload();

        expect(await page.getFileProgressById(fileId)).toEqual(stoppedProgress);
        expect(await page.getFileSpeedById(fileId)).toEqual("");
        expect(await page.getFileEtaById(fileId)).toEqual("");
        expect(await page.getFileStatusById(fileId)).toEqual("Stopped");

        await page.selectFileById(fileId);
        const reloadedQueueAction = await page.getFileActionByTitle(fileId, "Queue");
        const reloadedStopAction = await page.getFileActionByTitle(fileId, "Stop");
        expect(reloadedQueueAction.title).toBe("Queue");
        expect(reloadedQueueAction.isEnabled).toBe(true);
        expect(reloadedStopAction.title).toBe("Stop");
        expect(reloadedStopAction.isEnabled).toBe(false);

        await page.queueFileById(fileId);
        await page.waitForFileStatusNotById(fileId, "Stopped");

        expect(await page.getFileStatusById(fileId)).not.toEqual("Stopped");

        const requeuedQueueAction = await page.getFileActionByTitle(fileId, "Queue");
        const requeuedStopAction = await page.getFileActionByTitle(fileId, "Stop");
        expect(requeuedQueueAction.title).toBe("Queue");
        expect(requeuedQueueAction.isEnabled).toBe(false);
        expect(requeuedStopAction.title).toBe("Stop");
        expect(requeuedStopAction.isEnabled).toBe(true);

        if (await page.getFileStatusById(fileId) === "Downloading") {
            expect(await page.getFileSpeedById(fileId)).not.toEqual("");
            expect(await page.getFileEtaById(fileId)).not.toEqual("");
        }
    });
});
