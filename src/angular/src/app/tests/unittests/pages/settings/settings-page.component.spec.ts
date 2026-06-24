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
import {PathPair, PathPairService} from "../../../../services/settings/path-pair.service";


class MockConfigService {
    private _config = new BehaviorSubject<Config>(null);

    get config() {
        return this._config.asObservable();
    }
}

class MockConnectedService {
    connected = new BehaviorSubject(false);
}

class MockPathPairService {
    private _pathPairs = new BehaviorSubject<PathPair[]>([]);

    get pathPairs() {
        return this._pathPairs.asObservable();
    }

    push(pathPairs: PathPair[]) {
        this._pathPairs.next(pathPairs);
    }
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
                {provide: PathPairService, useClass: MockPathPairService},
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

    it("should expose the log format and log level options in other settings", () => {
        const logFormatOption = component.OPTIONS_CONTEXT_OTHER.options[2]!;
        const logLevelOption = component.OPTIONS_CONTEXT_OTHER.options[3]!;

        expect(logFormatOption.label).toBe("Log Format");
        expect(logFormatOption.valuePath).toEqual(["logging", "log_format"]);
        expect(logFormatOption.choices![0]).toEqual({label: "Standard", value: "standard"});
        expect(logFormatOption.choices![1]).toEqual({label: "JSON", value: "json"});

        expect(logLevelOption.label).toBe("Log Level");
        expect(logLevelOption.valuePath).toEqual(["general", "log_level"]);
        expect(logLevelOption.choices![0]).toEqual({label: "Debug", value: "DEBUG"});
    });

    it("should disable legacy directory and autoqueue toggles when path pairs are enabled", () => {
        const pathPairService = TestBed.get(PathPairService) as MockPathPairService;
        pathPairService.push([
            {
                id: "movies",
                name: "Movies",
                remote_path: "/remote/movies",
                local_path: "/downloads/movies",
                enabled: true,
                auto_queue: true
            }
        ]);
        fixture.detectChanges();

        const serverDirectory = component.serverContext.options.find(option => option.valuePath[1] === "remote_path")!;
        const localDirectory = component.serverContext.options.find(option => option.valuePath[1] === "local_path")!;
        const autoqueueEnabled = component.autoqueueContext.options.find(option => option.valuePath[1] === "enabled")!;
        const autoqueuePatternsOnly = component.autoqueueContext.options.find(option => option.valuePath[1] === "patterns_only")!;

        expect(serverDirectory.disabled).toBe(true);
        expect(serverDirectory.description).toContain("Path pairs override");
        expect(localDirectory.disabled).toBe(true);
        expect(localDirectory.description).toContain("Path pairs override");
        expect(autoqueueEnabled.disabled).toBe(true);
        expect(autoqueueEnabled.description).toContain("Path pairs override");
        expect(autoqueuePatternsOnly.disabled).toBeUndefined();

        pathPairService.push([]);
        fixture.detectChanges();

        expect(component.serverContext.options.find(option => option.valuePath[1] === "remote_path")!.disabled).toBeUndefined();
        expect(component.serverContext.options.find(option => option.valuePath[1] === "local_path")!.disabled).toBeUndefined();
        expect(component.autoqueueContext.options.find(option => option.valuePath[1] === "enabled")!.disabled).toBeUndefined();
    });
});
