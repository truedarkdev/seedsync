import {fakeAsync, TestBed, tick} from "@angular/core/testing";

import {VersionCheckService} from "../../../../services/utils/version-check.service";
import {RestService, WebReaction} from "../../../../services/utils/rest.service";
import {NotificationService} from "../../../../services/utils/notification.service";
import {Notification} from "../../../../services/utils/notification";
import {LoggerService} from "../../../../services/utils/logger.service";
import {MockRestService} from "../../../mocks/mock-rest.service";
import {Subject} from "rxjs";

declare function require(moduleName: string): any;
const {version: appVersion} = require("../../../../../../package.json");
const knownNewerReleaseTag = "v99.0.0";


describe("Testing version check service", () => {
    let versionCheckService: VersionCheckService;
    let notifService: NotificationService;
    let restService: RestService;

    let sendRequestSpy = null;

    beforeEach(() => {
        TestBed.configureTestingModule({
            providers: [
                VersionCheckService,
                LoggerService,
                NotificationService,
                {provide: RestService, useClass: MockRestService},
            ]
        });

        notifService = TestBed.get(NotificationService);
        restService = TestBed.get(RestService);

        spyOn(notifService, "show").and.callThrough();
        sendRequestSpy = spyOn(restService, "sendRequest").and.returnValue(
            new Subject<WebReaction>());

        versionCheckService = TestBed.get(VersionCheckService);
    });

    function createVersionCheckService(): VersionCheckService {
        return new VersionCheckService(
            restService,
            notifService,
            TestBed.get(LoggerService)
        );
    }

    it("should create an instance", () => {
        expect(versionCheckService).toBeDefined();
    });

    it("should request the correct github url", fakeAsync(() => {
        expect(restService.sendRequest).toHaveBeenCalledWith(
            "https://api.github.com/repos/ipsingh06/seedsync/releases/latest"
        );
    }));

    it("should stop waiting when the github request stalls", fakeAsync(() => {
        const subject = new Subject<WebReaction>();
        sendRequestSpy.and.returnValue(subject);

        // Recreate the service
        versionCheckService = createVersionCheckService();
        tick(10001);
        subject.next(new WebReaction(true, JSON.stringify({
            "tag_name": knownNewerReleaseTag,
            "html_url": `https://example.invalid/releases/${knownNewerReleaseTag}`
        }), null));
        tick();

        expect(notifService.show).not.toHaveBeenCalled();
    }));

    it("should fail gracefully on failed request to github", fakeAsync(() => {
        const subject = new Subject<WebReaction>();
        sendRequestSpy.and.returnValue(subject);

        // Recreate the service
        versionCheckService = createVersionCheckService();
        subject.next(new WebReaction(false, null, "some error"));
        tick();

        expect(notifService.show).not.toHaveBeenCalled();
    }));

    it("should fail gracefully on garbage data from github", fakeAsync(() => {
        const subject = new Subject<WebReaction>();
        sendRequestSpy.and.returnValue(subject);

        // Recreate the service
        versionCheckService = createVersionCheckService();
        subject.next(new WebReaction(true, "garbage data", null));
        tick();

        expect(notifService.show).not.toHaveBeenCalled();
    }));

    it("should fire a notification on new version", fakeAsync(() => {
        const subject = new Subject<WebReaction>();
        sendRequestSpy.and.returnValue(subject);
        const notifications: Notification[] = [];
        notifService.notifications.subscribe({
            next: list => {
                notifications.splice(0, notifications.length, ...list.toArray());
            }
        });

        // Recreate the service
        versionCheckService = createVersionCheckService();
        const releaseTag = knownNewerReleaseTag;
        const releaseUrl = `https://example.invalid/releases/${releaseTag}`;
        subject.next(new WebReaction(true, JSON.stringify({
            "tag_name": releaseTag,
            "html_url": releaseUrl
        }), null));
        tick();

        expect(notifications.length).toBe(1);
        expect(notifications[0].text).toContain(releaseUrl);
        expect(notifService.show).toHaveBeenCalled();
    }));

    it("should not fire a notification when github reports the current version", fakeAsync(() => {
        const subject = new Subject<WebReaction>();
        sendRequestSpy.and.returnValue(subject);

        // Recreate the service
        versionCheckService = createVersionCheckService();
        subject.next(new WebReaction(true, JSON.stringify({
            "tag_name": `v${appVersion}`,
            "html_url": `https://example.invalid/releases/v${appVersion}`
        }), null));
        tick();

        expect(notifService.show).not.toHaveBeenCalled();
    }));

    it("should not fire a notification on old version", fakeAsync(() => {
        const subject = new Subject<WebReaction>();
        sendRequestSpy.and.returnValue(subject);

        // Recreate the service
        versionCheckService = createVersionCheckService();
        subject.next(new WebReaction(true, JSON.stringify({
            "tag_name": "v0.0-0",
            "html_url": "https://example.invalid/releases/v0.0-0"
        }), null));
        tick();

        expect(notifService.show).not.toHaveBeenCalled();
    }));
});
