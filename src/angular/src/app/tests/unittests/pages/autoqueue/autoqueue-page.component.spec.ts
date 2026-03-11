import {ComponentFixture, TestBed} from "@angular/core/testing";
import {FormsModule} from "@angular/forms";
import {BehaviorSubject} from "rxjs/Rx";
import {Observable} from "rxjs/Observable";
import "rxjs/add/observable/of";

import * as Immutable from "immutable";

import {AutoQueuePageComponent} from "../../../../pages/autoqueue/autoqueue-page.component";
import {AutoQueueService} from "../../../../services/autoqueue/autoqueue.service";
import {AutoQueuePattern} from "../../../../services/autoqueue/autoqueue-pattern";
import {NotificationService} from "../../../../services/utils/notification.service";
import {ConfigService} from "../../../../services/settings/config.service";
import {ConnectedService} from "../../../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../../../services/base/stream-service.registry";
import {Config} from "../../../../services/settings/config";
import {WebReaction} from "../../../../services/utils/rest.service";


class MockAutoQueueService {
    private _patterns = new BehaviorSubject(Immutable.List<AutoQueuePattern>([]));

    get patterns() {
        return this._patterns.asObservable();
    }

    add = jasmine.createSpy("add").and.returnValue(Observable.of(new WebReaction(true, "ok", null)));
    remove = jasmine.createSpy("remove").and.returnValue(Observable.of(new WebReaction(true, "ok", null)));
}

class MockNotificationService {
    show = jasmine.createSpy("show");
}

class MockConfigService {
    private _config = new BehaviorSubject<Config>(null);

    get config() {
        return this._config.asObservable();
    }

    push(config: Config) {
        this._config.next(config);
    }
}

class MockConnectedService {
    connected = new BehaviorSubject(false);
}

class MockStreamServiceRegistry {
    connectedService = TestBed.get(ConnectedService);
}

function createConfig(enabled: boolean, patternsOnly: boolean): Config {
    return new Config({
        autoqueue: {
            enabled: enabled,
            patterns_only: patternsOnly
        }
    });
}

describe("Testing autoqueue page component", () => {
    let component: AutoQueuePageComponent;
    let fixture: ComponentFixture<AutoQueuePageComponent>;
    let autoQueueService: MockAutoQueueService;
    let configService: MockConfigService;
    let connectedService: MockConnectedService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            declarations: [AutoQueuePageComponent],
            imports: [FormsModule],
            providers: [
                {provide: AutoQueueService, useClass: MockAutoQueueService},
                {provide: NotificationService, useClass: MockNotificationService},
                {provide: ConfigService, useClass: MockConfigService},
                {provide: ConnectedService, useClass: MockConnectedService},
                {provide: StreamServiceRegistry, useClass: MockStreamServiceRegistry}
            ]
        });

        fixture = TestBed.createComponent(AutoQueuePageComponent);
        component = fixture.componentInstance;
        autoQueueService = TestBed.get(AutoQueueService);
        configService = TestBed.get(ConfigService);
        connectedService = TestBed.get(ConnectedService);

        fixture.detectChanges();
    });

    afterEach(() => {
        fixture.destroy();
    });

    it("should not add a pattern when editing is disabled", () => {
        connectedService.connected.next(false);
        configService.push(createConfig(true, true));
        component.newPattern = "show-*";

        component.onAddPattern();

        expect(autoQueueService.add).not.toHaveBeenCalled();
    });

    it("should add a pattern when editing is enabled", () => {
        connectedService.connected.next(true);
        configService.push(createConfig(true, true));
        component.newPattern = "show-*";

        component.onAddPattern();

        expect(autoQueueService.add).toHaveBeenCalledWith("show-*");
        expect(component.newPattern).toBe("");
    });

    it("should not remove a pattern when editing is disabled", () => {
        connectedService.connected.next(true);
        configService.push(createConfig(false, false));

        component.onRemovePattern(new AutoQueuePattern({pattern: "show-*"}));

        expect(autoQueueService.remove).not.toHaveBeenCalled();
    });
});
