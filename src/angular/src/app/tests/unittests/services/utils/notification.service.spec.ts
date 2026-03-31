import {fakeAsync, TestBed, tick} from "@angular/core/testing";

import * as Immutable from "immutable";

import {NotificationService} from "../../../../services/utils/notification.service";
import {LoggerService} from "../../../../services/utils/logger.service";
import {Notification} from "../../../../services/utils/notification";

class TestNotificationService extends NotificationService {

}

describe("Testing notification service", () => {
    let notificationService: TestNotificationService;

    function createNotification(level: Notification.Level, text: string, timestamp?: number): Notification {
        const notification = new Notification({level: level, text: text});
        return timestamp == null ? notification : notification.set("timestamp", timestamp) as Notification;
    }

    beforeEach(() => {
        TestBed.configureTestingModule({
            providers: [
                LoggerService,
                {provide: NotificationService, useClass: TestNotificationService},
            ]
        });

        notificationService = TestBed.get(NotificationService);
    });


    it("should create an instance", () => {
        expect(notificationService).toBeDefined();
    });

    it("should show notification", fakeAsync(() => {
        const expectedNotification = createNotification(Notification.Level.DANGER, "danger");

        notificationService.show(expectedNotification);

        let actualCount = 0;
        notificationService.notifications.subscribe({
            next: list => {
                expect(list.size).toBe(1);
                expect(Immutable.is(expectedNotification, list.get(0))).toBe(true);
                actualCount++;
            }
        });

        tick();
        expect(actualCount).toBe(1);
    }));

    it("should hide notification", fakeAsync(() => {
        const expectedNotification = createNotification(Notification.Level.DANGER, "danger");

        notificationService.show(expectedNotification);
        tick();
        notificationService.hide(expectedNotification);

        let actualCount = 0;
        notificationService.notifications.subscribe({
            next: list => {
                expect(list.size).toBe(0);
                actualCount++;
            }
        });

        tick();
        expect(actualCount).toBe(1);
    }));


    it("should only send one update if show is called twice", fakeAsync(() => {
        const expectedNotification = createNotification(Notification.Level.DANGER, "danger");

        notificationService.show(expectedNotification);

        let actualCount = 0;
        // noinspection JSUnusedLocalSymbols
        notificationService.notifications.subscribe({
            next: list => {
                actualCount++;
            }
        });
        tick();
        notificationService.show(expectedNotification);
        tick();

        expect(actualCount).toBe(1);
    }));

    it("should only send one update if hide is called twice", fakeAsync(() => {
        const expectedNotification = createNotification(Notification.Level.DANGER, "danger");

        notificationService.show(expectedNotification);
        tick();
        notificationService.hide(expectedNotification);

        let actualCount = 0;
        // noinspection JSUnusedLocalSymbols
        notificationService.notifications.subscribe({
            next: list => {
                actualCount++;
            }
        });

        tick();
        notificationService.hide(expectedNotification);
        tick();

        expect(actualCount).toBe(1);
    }));

    it("should sort notifications by level", fakeAsync(() => {
        const noteDanger = createNotification(Notification.Level.DANGER, "danger");
        const noteInfo = createNotification(Notification.Level.INFO, "info");
        const noteWarning = createNotification(Notification.Level.WARNING, "warning");
        const noteSuccess = createNotification(Notification.Level.SUCCESS, "success");

        notificationService.show(noteDanger);
        notificationService.show(noteInfo);
        notificationService.show(noteWarning);
        notificationService.show(noteSuccess);

        let actualCount = 0;
        notificationService.notifications.subscribe({
            next: list => {
                expect(list.size).toBe(4);
                expect(Immutable.is(noteDanger, list.get(0))).toBe(true);
                expect(Immutable.is(noteWarning, list.get(1))).toBe(true);
                expect(Immutable.is(noteInfo, list.get(2))).toBe(true);
                expect(Immutable.is(noteSuccess, list.get(3))).toBe(true);
                actualCount++;
            }
        });

        tick();
        expect(actualCount).toBe(1);
    }));

    it("should sort notifications by timestamp", fakeAsync(() => {
        const noteOlder = createNotification(Notification.Level.DANGER, "older", 100);
        const noteNewer = createNotification(Notification.Level.DANGER, "newer", 200);
        const noteNewest = createNotification(Notification.Level.DANGER, "newest", 300);

        notificationService.show(noteNewer);
        notificationService.show(noteNewest);
        notificationService.show(noteOlder);

        let actualCount = 0;
        notificationService.notifications.subscribe({
            next: list => {
                expect(list.size).toBe(3);
                expect(Immutable.is(noteNewest, list.get(0))).toBe(true);
                expect(Immutable.is(noteNewer, list.get(1))).toBe(true);
                expect(Immutable.is(noteOlder, list.get(2))).toBe(true);
                actualCount++;
            }
        });

        tick();
        expect(actualCount).toBe(1);
    }));

    it("should sort notifications by level first, then timestamp", fakeAsync(() => {
        const noteOlder = createNotification(Notification.Level.DANGER, "older", 100);
        const noteNewer = createNotification(Notification.Level.INFO, "newer", 200);
        const noteNewest = createNotification(Notification.Level.INFO, "newest", 300);

        notificationService.show(noteNewer);
        notificationService.show(noteNewest);
        notificationService.show(noteOlder);

        let actualCount = 0;
        notificationService.notifications.subscribe({
            next: list => {
                expect(list.size).toBe(3);
                expect(Immutable.is(noteOlder, list.get(0))).toBe(true);
                expect(Immutable.is(noteNewest, list.get(1))).toBe(true);
                expect(Immutable.is(noteNewer, list.get(2))).toBe(true);
                actualCount++;
            }
        });

        tick();
        expect(actualCount).toBe(1);
    }));
});
