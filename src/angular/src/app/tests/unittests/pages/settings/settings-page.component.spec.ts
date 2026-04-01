import {NO_ERRORS_SCHEMA} from "@angular/core";
import {ComponentFixture, TestBed, fakeAsync, flushMicrotasks} from "@angular/core/testing";
import {BehaviorSubject, of} from "rxjs";
import {Modal} from "../../../../services/utils/modal.service";

import {SettingsPageComponent} from "../../../../pages/settings/settings-page.component";
import {ConfigService} from "../../../../services/settings/config.service";
import {Config} from "../../../../services/settings/config";
import {ConnectedService} from "../../../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../../../services/base/stream-service.registry";
import {LoggerService} from "../../../../services/utils/logger.service";
import {NotificationService} from "../../../../services/utils/notification.service";
import {ServerCommandService} from "../../../../services/server/server-command.service";
import {ModalAccessibilityService} from "../../../../services/utils/modal-accessibility.service";


class MockConfigService {
    private _config = new BehaviorSubject<Config>(null);

    get config() {
        return this._config.asObservable();
    }
}

class MockConnectedService {
    connected = new BehaviorSubject(false);
}

class MockStreamServiceRegistry {
    connectedService = TestBed.get(ConnectedService);
}

class MockLoggerService {
    info = jasmine.createSpy("info");
    error = jasmine.createSpy("error");
    level = 0;
}

class MockNotificationService {
    show = jasmine.createSpy("show");
    hide = jasmine.createSpy("hide");
}

class MockServerCommandService {
    restart = jasmine.createSpy("restart").and.returnValue(of({
        success: true,
        data: "ok",
        errorMessage: null
    }));
}

class MockDialogResult {
    then(resolve: Function, reject: Function) {
        resolve();
        return this;
    }
}

class MockDialogBuilder {
    title() { return this; }
    okBtn() { return this; }
    okBtnClass() { return this; }
    cancelBtn() { return this; }
    cancelBtnClass() { return this; }
    isBlocking() { return this; }
    showClose() { return this; }
    body() { return this; }
    open() {
        return Promise.resolve({
            result: new MockDialogResult()
        });
    }
}

class MockModal {
    confirm() {
        return new MockDialogBuilder();
    }
}

class MockModalAccessibilityService {
    enhance = jasmine.createSpy("enhance").and.callFake((dialogRefPromise: Promise<any>) => dialogRefPromise);
}

describe("Testing settings page component", () => {
    let component: SettingsPageComponent;
    let fixture: ComponentFixture<SettingsPageComponent>;
    let commandService: MockServerCommandService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            declarations: [SettingsPageComponent],
            schemas: [NO_ERRORS_SCHEMA],
            providers: [
                {provide: LoggerService, useClass: MockLoggerService},
                {provide: ConfigService, useClass: MockConfigService},
                {provide: NotificationService, useClass: MockNotificationService},
                {provide: ServerCommandService, useClass: MockServerCommandService},
                {provide: Modal, useClass: MockModal},
                {provide: ModalAccessibilityService, useClass: MockModalAccessibilityService},
                {provide: ConnectedService, useClass: MockConnectedService},
                {provide: StreamServiceRegistry, useClass: MockStreamServiceRegistry}
            ]
        });

        fixture = TestBed.createComponent(SettingsPageComponent);
        component = fixture.componentInstance;
        commandService = TestBed.get(ServerCommandService);
        fixture.detectChanges();
    });

    it("should not start a restart request after destroy while the modal promise resolves", fakeAsync(() => {
        component.onCommandRestart();
        component.ngOnDestroy();

        flushMicrotasks();

        expect(commandService.restart).not.toHaveBeenCalled();
    }));
});
