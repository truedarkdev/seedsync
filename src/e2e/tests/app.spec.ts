import {App} from "./app";

describe('Testing top-level app', () => {
    let app: App;

    beforeEach(async () => {
        app = new App();
        await app.navigateTo();
    });

    it('should have right title', async () => {
        expect(await app.getTitle()).toEqual("SeedSync");
    });

    it('should have all the sidebar items', async () => {
        expect(await app.getSidebarItems()).toEqual(
            [
                "Dashboard",
                "Settings",
                "Logs",
                "About",
                "Restart"
            ]
        );
    });

    it('should default to the dashboard page', async () => {
        expect(await app.getTopTitle()).toEqual("Dashboard");
    });
});
