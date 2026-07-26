import {NgZone} from "@angular/core";
import {ComponentFixture, TestBed} from "@angular/core/testing";
import {of, Subject, throwError} from "rxjs";

import {MigrationAppComponent} from "../../../../pages/migration/migration-app.component";
import {MigrationStatus} from "../../../../services/migration/migration.model";
import {MigrationService} from "../../../../services/migration/migration.service";


describe("MigrationAppComponent", () => {
    let fixture: ComponentFixture<MigrationAppComponent>;
    let service: jasmine.SpyObj<MigrationService>;

    const status: MigrationStatus = {
        schema_version: 2,
        mode: "migration_required",
        state: "required",
        migration_id: "original-v0.8.6-to-current-v1",
        source_schema: "original-v0.8.6",
        target_schema: "current-v1",
        features: [
            {key: "path-pairs", title: "Sync more than one folder", summary: "Keep separate remote and local folders."},
            {key: "accurate-progress", title: "Progress you can trust", summary: "See live byte progress."},
            {key: "secure-access", title: "Safer local access", summary: "Claim a trusted browser."},
            {key: "notifications", title: "Notifications where you want them", summary: "Choose delivery events."},
            {key: "transfer-choices", title: "Choose your transfer engine", summary: "Use LFTP or rclone."},
            {key: "historical-logs", title: "Find problems faster", summary: "Search retained logs."}
        ],
        error: null,
        retryable: false,
        capabilities: {apply: true, retry: false, restore: false},
        backup: {required: true, complete_restore_ready: false, status: "created_before_apply"},
        operation: {status: "idle", message: "Ready to migrate."},
        action: {csrf_token: "csrf-proof-0123456789-0123456789", confirmation: "MIGRATE original-v0.8.6-to-current-v1"},
        blocker: null
    };

    beforeEach(() => {
        service = jasmine.createSpyObj<MigrationService>("MigrationService", ["loadStatus", "apply"]);
        TestBed.configureTestingModule({
            imports: [MigrationAppComponent],
            providers: [{provide: MigrationService, useValue: service}]
        });
    });

    function createComponent(): void {
        fixture = TestBed.createComponent(MigrationAppComponent);
        fixture.detectChanges();
    }

    it("renders an asynchronously fetched required state and confirmation-gated migration action", async () => {
        const response = new Subject<MigrationStatus>();
        service.loadStatus.and.returnValue(response.asObservable());
        createComponent();
        fixture.autoDetectChanges();

        expect(fixture.nativeElement.textContent).toContain("Checking migration status");

        TestBed.get(NgZone).runOutsideAngular(() => response.next(status));
        await fixture.whenStable();

        const text = fixture.nativeElement.textContent;
        const startButton: HTMLButtonElement = fixture.nativeElement.querySelector(".primary-button");
        expect(text).toContain("Migration required");
        expect(text).not.toContain("Migration state: required");
        expect(text).toContain("v0.8.6");
        expect(text).toContain("v0.9.0");
        expect(text).not.toContain("original-v0.8.6");
        expect(text).toContain("Complete retained backup before migration");
        expect(text).toContain("will create, fsync, and validate a complete retained backup");
        expect(startButton.disabled).toBeTrue();
        expect(fixture.nativeElement.querySelector("app-sidebar")).toBeNull();
        expect(fixture.nativeElement.querySelector(".feature-copy h2").textContent).toContain("Sync more than one folder");
        expect(fixture.nativeElement.querySelector(".feature-viewport img").getAttribute("src"))
            .toBe("/assets/migration/path-pairs.png");
        expect(fixture.nativeElement.querySelector(".feature-viewport img").classList).toContain("image-contain");
        const imageControls: HTMLElement = fixture.nativeElement.querySelector(".feature-image-controls");
        const imageSelectors: NodeListOf<HTMLButtonElement> = imageControls.querySelectorAll("button");
        expect(imageControls.textContent.trim()).toBe("");
        expect(imageControls.getAttribute("aria-label"))
            .toBe("Image 1 of 2 for Sync more than one folder");
        expect(imageSelectors.length).toBe(2);
        expect(imageSelectors[0].getAttribute("aria-label")).toBe("Show path-pair setup image");
        expect(imageSelectors[0].getAttribute("aria-current")).toBe("true");
        expect(imageSelectors[0].getAttribute("aria-pressed")).toBe("true");
        expect(imageSelectors[1].getAttribute("aria-label")).toBe("Show path-pair overview image");
        expect(imageSelectors[1].getAttribute("aria-current")).toBeNull();
        expect(imageSelectors[1].getAttribute("aria-pressed")).toBe("false");
        expect(fixture.nativeElement.querySelectorAll(".slide-controls button").length).toBe(7);
        expect(fixture.componentInstance.featureSlides.map(feature => feature.key)).toEqual([
            "path-pairs",
            "accurate-progress",
            "large-queues",
            "secure-access",
            "notifications",
            "transfer-choices",
            "historical-logs"
        ]);
        const autoPageInput: HTMLInputElement = fixture.nativeElement.querySelector(".auto-page-control input");
        expect(autoPageInput.checked).toBeTrue();
        expect(autoPageInput.getAttribute("aria-label")).toBe("Automatically advance feature slides");
        expect(fixture.nativeElement.querySelector(".auto-page-control").textContent)
            .toContain("Auto-advance slides");
    });

    it("shows a recheck-only error state for malformed or unavailable status", () => {
        service.loadStatus.and.returnValue(throwError(() => new Error("malformed")));
        createComponent();

        expect(fixture.nativeElement.textContent).toContain("Status unavailable");
        expect(fixture.nativeElement.querySelector(".primary-button")).toBeNull();
        expect(fixture.nativeElement.querySelector(".secondary-button").textContent).toContain("Recheck status");
    });

    it("requires confirmation, submits the guarded action, and enters running polling state", () => {
        service.loadStatus.and.returnValue(of(status));
        service.apply.and.returnValue(of({
            ...status,
            state: "running",
            capabilities: {apply: false, retry: false, restore: false},
            operation: {status: "running", message: "Migration running."},
            blocker: "migration_running"
        }));
        createComponent();

        const checkbox: HTMLInputElement = fixture.nativeElement.querySelector(".migration-confirmation input");
        const button: HTMLButtonElement = fixture.nativeElement.querySelector(".primary-button");
        expect(button.disabled).toBeTrue();
        checkbox.checked = true;
        checkbox.dispatchEvent(new Event("change"));
        fixture.detectChanges();
        expect(button.disabled).toBeFalse();
        button.click();
        fixture.detectChanges();

        expect(service.apply).toHaveBeenCalledWith(status);
        expect(fixture.componentInstance.status?.operation.status).toBe("running");
        expect(fixture.nativeElement.textContent).toContain("Migration running");
        fixture.destroy();
    });

    it("renders retryable failure with confirmation-gated retry", () => {
        service.loadStatus.and.returnValue(of({
            ...status,
            state: "failed",
            retryable: true,
            capabilities: {apply: false, retry: true, restore: false},
            operation: {status: "failed", message: "Stopped safely."},
            error: {code: "migration_preflight_failed", message: "Readiness check failed."}
        }));
        createComponent();

        expect(fixture.nativeElement.textContent).toContain("Migration readiness check failed");
        expect(fixture.nativeElement.textContent).toContain("retained backup remains available");
        expect(fixture.nativeElement.querySelector(".migration-route").textContent).toContain("v0.8.6");
        expect(fixture.nativeElement.querySelector(".migration-route").textContent).toContain("v0.9.0");
        expect(fixture.nativeElement.querySelector(".primary-button").textContent).toContain("Retry migration");
    });

    it("does not claim the v0.8.6 route for an unrecognized required source", () => {
        service.loadStatus.and.returnValue(of({...status, source_schema: null}));
        createComponent();

        expect(fixture.nativeElement.textContent).toContain("Migration reassessment required");
        expect(fixture.nativeElement.textContent).toContain("could not confirm a supported migration source");
        expect(fixture.nativeElement.textContent).not.toContain("v0.8.6");
        expect(fixture.nativeElement.querySelector(".migration-route")).toBeNull();
        expect(fixture.nativeElement.querySelector(".primary-button")).toBeNull();
    });

    it("renders polished running and complete states with state-appropriate backup copy", () => {
        for (const stateName of ["running", "complete"] as const) {
            service.loadStatus.and.returnValue(of({
                ...status,
                state: stateName,
                capabilities: {apply: false, retry: false, restore: false},
                operation: {
                    status: stateName === "running" ? "running" : "succeeded",
                    message: stateName
                }
            }));
            createComponent();

            const text = fixture.nativeElement.textContent;
            if (stateName === "running") {
                expect(text).toContain("Migration in progress");
                expect(text).toContain("Creating and validating retained backup");
                expect(text).not.toContain("Migration state: running");
            } else {
                expect(text).toContain("Migration complete");
                expect(text).toContain("Retained backup ready");
                expect(text).not.toContain("Migration state: complete");
                expect(text).not.toContain("Complete retained backup before migration");
            }
            const route: HTMLElement = fixture.nativeElement.querySelector(".migration-route");
            expect(route.textContent).toContain("v0.8.6");
            expect(route.textContent).toContain("v0.9.0");
            expect(fixture.nativeElement.querySelector(".primary-button")).toBeNull();
            expect(fixture.nativeElement.querySelector(".secondary-button").textContent).toContain("Recheck status");
            fixture.destroy();
        }
    });

    it("keeps the version transition and stable shell contract across migration states", () => {
        for (const stateName of ["required", "running", "failed", "complete"] as const) {
            service.loadStatus.and.returnValue(of({
                ...status,
                state: stateName,
                capabilities: stateName === "required"
                    ? status.capabilities
                    : {apply: false, retry: stateName === "failed", restore: false},
                operation: {
                    status: stateName === "required" ? "idle"
                        : stateName === "complete" ? "succeeded" : stateName,
                    message: stateName
                }
            }));
            createComponent();

            const shell: HTMLElement = fixture.nativeElement.querySelector(".migration-shell");
            const route: HTMLElement = fixture.nativeElement.querySelector(".migration-route");
            const source: HTMLElement = route.querySelector(".migration-route-source");
            const target: HTMLElement = route.querySelector(".migration-route-target");
            expect(shell.classList).toContain("migration-shell--state-stable");
            expect(route).not.toBeNull();
            expect(route.textContent).toContain("v0.8.6");
            expect(route.textContent).toContain("v0.9.0");
            if (stateName === "complete") {
                expect(route.classList).toContain("is-complete");
                expect(route.getAttribute("aria-label")).toBe(
                    "Version transition complete: previous version v0.8.6; current version v0.9.0."
                );
                expect(source.querySelector("small").textContent).toBe("Previous version");
                expect(target.querySelector("small").textContent).toBe("Current version");
                const versionSuccessMark: HTMLElement = target.querySelector("strong > .version-success-mark");
                expect(versionSuccessMark).not.toBeNull();
                expect(versionSuccessMark.getAttribute("aria-hidden")).toBe("true");
                expect(target.querySelector("strong > .version-value").textContent).toBe("v0.9.0");
                expect(target.querySelector("strong").textContent).toContain("✓v0.9.0");
                const successMark: HTMLElement = fixture.nativeElement.querySelector(".success-mark");
                expect(successMark.getAttribute("role")).toBe("img");
                expect(successMark.getAttribute("aria-label")).toBe("Migration succeeded");
                expect(fixture.nativeElement.querySelector(".status-heading-row").classList)
                    .toContain("is-success");
            } else {
                expect(route.classList).not.toContain("is-complete");
                expect(route.getAttribute("aria-label")).toBe(
                    "Version transition: detected source v0.8.6; migration target v0.9.0."
                );
                expect(source.querySelector("small").textContent).toBe("Detected source");
                expect(target.querySelector("small").textContent).toBe("Migration target");
                expect(target.querySelector("strong > .version-success-mark")).toBeNull();
                expect(target.querySelector("strong > .version-value").textContent).toBe("v0.9.0");
                expect(fixture.nativeElement.querySelector(".success-mark")).toBeNull();
                expect(fixture.nativeElement.querySelector(".status-heading-row").classList)
                    .not.toContain("is-success");
            }
            fixture.destroy();
        }
    });

    it("disables automatic paging after next, previous, or dot navigation", () => {
        jasmine.clock().install();
        try {
            service.loadStatus.and.returnValue(of(status));
            createComponent();
            const component = fixture.componentInstance;
            const autoPageInput = (): HTMLInputElement =>
                fixture.nativeElement.querySelector(".auto-page-control input");

            const nextButton: HTMLButtonElement = fixture.nativeElement.querySelector('[aria-label="Next feature"]');
            nextButton.click();
            fixture.detectChanges();
            expect(component.activeFeatureIndex).toBe(1);
            expect(component.autoAdvanceEnabled).toBeFalse();
            expect(autoPageInput().checked).toBeFalse();
            expect(fixture.nativeElement.querySelector(".feature-copy h2").textContent).toContain("Progress you can trust");

            component.setAutoAdvanceEnabled(true);
            const previousButton: HTMLButtonElement = fixture.nativeElement.querySelector('[aria-label="Previous feature"]');
            previousButton.click();
            fixture.detectChanges();
            expect(component.activeFeatureIndex).toBe(0);
            expect(component.autoAdvanceEnabled).toBeFalse();
            expect(autoPageInput().checked).toBeFalse();

            component.setAutoAdvanceEnabled(true);
            const thirdDot: HTMLButtonElement = fixture.nativeElement.querySelectorAll(".slide-controls button")[2];
            thirdDot.click();
            fixture.detectChanges();
            expect(component.activeFeatureIndex).toBe(2);
            expect(component.autoAdvanceEnabled).toBeFalse();
            expect(autoPageInput().checked).toBeFalse();
        } finally {
            jasmine.clock().uninstall();
        }
    });

    it("auto-advances after ten seconds, restarts when re-enabled, and wraps", () => {
        jasmine.clock().install();
        try {
            service.loadStatus.and.returnValue(of(status));
            createComponent();
            const component = fixture.componentInstance;

            jasmine.clock().tick(4999);
            fixture.detectChanges();
            expect(component.activeFeatureIndex).toBe(0);
            expect(component.pathPairOverviewVisible).toBeFalse();

            jasmine.clock().tick(1);
            fixture.detectChanges();
            expect(component.pathPairOverviewVisible).toBeTrue();

            jasmine.clock().tick(4999);
            fixture.detectChanges();
            expect(component.activeFeatureIndex).toBe(0);
            expect(component.pathPairOverviewVisible).toBeTrue();

            jasmine.clock().tick(1);
            fixture.detectChanges();
            expect(component.activeFeatureIndex).toBe(1);
            expect(component.autoAdvanceEnabled).toBeTrue();
            expect(fixture.nativeElement.querySelector(".feature-viewport img").getAttribute("src"))
                .toBe("/assets/migration/progress.png");

            component.setAutoAdvanceEnabled(false);
            jasmine.clock().tick(10000);
            expect(component.activeFeatureIndex).toBe(1);

            component.setAutoAdvanceEnabled(true);
            jasmine.clock().tick(9999);
            expect(component.activeFeatureIndex).toBe(1);
            jasmine.clock().tick(1);
            expect(component.activeFeatureIndex).toBe(2);

            component.showFeature(6);
            component.setAutoAdvanceEnabled(true);
            jasmine.clock().tick(10000);
            expect(component.activeFeatureIndex).toBe(0);
            expect(component.autoAdvanceEnabled).toBeTrue();
        } finally {
            jasmine.clock().uninstall();
        }
    });

    it("selects an inner image directly and restarts its five-second interval", () => {
        jasmine.clock().install();
        try {
            service.loadStatus.and.returnValue(of(status));
            createComponent();
            const component = fixture.componentInstance;
            const imageControls: HTMLElement = fixture.nativeElement.querySelector(".feature-image-controls");
            const overviewButton: HTMLButtonElement = imageControls.querySelectorAll("button")[1];

            jasmine.clock().tick(3000);
            overviewButton.click();
            fixture.detectChanges();
            expect(component.autoAdvanceEnabled).toBeFalse();
            expect(fixture.nativeElement.querySelector(".auto-page-control input").checked).toBeFalse();
            expect(component.pathPairOverviewVisible).toBeTrue();
            expect(imageControls.textContent.trim()).toBe("");
            expect(imageControls.getAttribute("aria-label"))
                .toBe("Image 2 of 2 for Sync more than one folder");
            expect(overviewButton.getAttribute("aria-current")).toBe("true");
            expect(overviewButton.getAttribute("aria-pressed")).toBe("true");

            jasmine.clock().tick(4999);
            expect(component.pathPairOverviewVisible).toBeTrue();
            jasmine.clock().tick(1);
            fixture.detectChanges();
            expect(component.pathPairOverviewVisible).toBeFalse();
            expect(imageControls.getAttribute("aria-label"))
                .toBe("Image 1 of 2 for Sync more than one folder");

            jasmine.clock().tick(5000);
            expect(component.pathPairOverviewVisible).toBeTrue();
            expect(component.activeFeatureIndex).toBe(0);
        } finally {
            jasmine.clock().uninstall();
        }
    });

    it("keeps the ten-second cadence while the feature viewport is hovered", () => {
        jasmine.clock().install();
        try {
            service.loadStatus.and.returnValue(of(status));
            createComponent();
            const component = fixture.componentInstance;
            const viewport: HTMLElement = fixture.nativeElement.querySelector(".feature-viewport");

            viewport.dispatchEvent(new MouseEvent("mouseenter"));
            jasmine.clock().tick(4999);
            fixture.detectChanges();
            expect(component.activeFeatureIndex).toBe(0);
            expect(component.pathPairOverviewVisible).toBeFalse();

            jasmine.clock().tick(1);
            fixture.detectChanges();
            expect(component.pathPairOverviewVisible).toBeTrue();

            jasmine.clock().tick(2000);
            viewport.dispatchEvent(new MouseEvent("mouseleave"));
            jasmine.clock().tick(2999);
            expect(component.activeFeatureIndex).toBe(0);
            jasmine.clock().tick(1);
            expect(component.activeFeatureIndex).toBe(1);
            expect(component.autoAdvanceEnabled).toBeTrue();
        } finally {
            jasmine.clock().uninstall();
        }
    });

    it("re-enables through the checkbox and advances while carousel controls retain focus", () => {
        jasmine.clock().install();
        try {
            service.loadStatus.and.returnValue(of(status));
            createComponent();
            const component = fixture.componentInstance;
            const input: HTMLInputElement = fixture.nativeElement.querySelector(".auto-page-control input");

            component.showFeature(0);
            fixture.detectChanges();
            expect(input.checked).toBeFalse();

            input.focus();
            input.click();
            fixture.detectChanges();
            expect(document.activeElement).toBe(input);
            expect(input.checked).toBeTrue();
            jasmine.clock().tick(9999);
            expect(component.activeFeatureIndex).toBe(0);
            jasmine.clock().tick(1);
            expect(component.activeFeatureIndex).toBe(1);

            const nextButton: HTMLButtonElement = fixture.nativeElement.querySelector('[aria-label="Next feature"]');
            nextButton.focus();
            jasmine.clock().tick(10000);
            expect(component.activeFeatureIndex).toBe(2);

            const firstDot: HTMLButtonElement = fixture.nativeElement.querySelectorAll(".slide-controls button")[0];
            firstDot.focus();
            jasmine.clock().tick(10000);
            expect(component.activeFeatureIndex).toBe(3);
        } finally {
            jasmine.clock().uninstall();
        }
    });

    it("applies keyed framing treatments to the corrected release visuals", () => {
        service.loadStatus.and.returnValue(of(status));
        createComponent();

        const expectedTreatments = [
            {index: 1, source: "/assets/migration/progress.png", className: "image-progress"},
            {index: 3, source: "/assets/migration/first-claim.png", className: "image-claim"},
            {index: 4, source: "/assets/migration/notifications.png", className: "image-notifications"},
            {index: 5, source: "/assets/migration/current-settings.png", className: "image-transfer"},
            {index: 6, source: "/assets/migration/historical-logs.png", className: "image-logs"}
        ];
        for (const expected of expectedTreatments) {
            fixture.componentInstance.showFeature(expected.index);
            fixture.detectChanges();
            const image: HTMLImageElement = fixture.nativeElement.querySelector(".feature-viewport img");
            expect(image.getAttribute("src")).toBe(expected.source);
            expect(image.classList).toContain(expected.className);
            expect(fixture.nativeElement.querySelector(".feature-image-controls")).toBeNull();
        }

        fixture.componentInstance.showFeature(2);
        fixture.detectChanges();
        const queueImage: HTMLImageElement = fixture.nativeElement.querySelector(".feature-viewport img");
        expect(fixture.nativeElement.querySelector(".feature-copy h2").textContent)
            .toContain("Large libraries, your way");
        expect(queueImage.getAttribute("src")).toBe("/assets/migration/large-queues.png");
        expect(queueImage.classList).toContain("image-queue");
        expect(fixture.nativeElement.querySelector(".feature-image-controls")).toBeNull();
    });

    it("continuously alternates path-pair images while outer paging is disabled", () => {
        jasmine.clock().install();
        try {
            service.loadStatus.and.returnValue(of(status));
            createComponent();
            const component = fixture.componentInstance;
            component.setAutoAdvanceEnabled(false);

            const expectOverview = (visible: boolean): void => {
                fixture.detectChanges();
                const images: NodeListOf<HTMLImageElement> =
                    fixture.nativeElement.querySelectorAll(".feature-viewport img");
                expect(images.length).toBe(2);
                expect(images[0].classList.contains("is-visible")).toBe(!visible);
                if (visible) {
                    expect(images[0].getAttribute("alt")).toBe("");
                } else {
                    expect(images[0].getAttribute("alt")).toContain("path-pair settings");
                }
                expect(images[0].getAttribute("aria-hidden")).toBe(visible ? "true" : null);
                expect(images[1].classList.contains("is-visible")).toBe(visible);
                if (visible) {
                    expect(images[1].getAttribute("alt")).toContain("Movies, Series, Music, and Books");
                } else {
                    expect(images[1].getAttribute("alt")).toBe("");
                }
                expect(images[1].getAttribute("aria-hidden")).toBe(visible ? null : "true");
            };

            expectOverview(false);
            jasmine.clock().tick(5000);
            expectOverview(true);
            jasmine.clock().tick(5000);
            expectOverview(false);
            jasmine.clock().tick(5000);
            expectOverview(true);
            expect(component.activeFeatureIndex).toBe(0);

            component.showFeature(1);
            component.showFeature(0);
            expectOverview(false);
            jasmine.clock().tick(5000);
            expectOverview(true);
            jasmine.clock().tick(5000);
            expectOverview(false);
            expect(component.activeFeatureIndex).toBe(0);

            fixture.destroy();
            jasmine.clock().tick(10000);
            expect(component.pathPairOverviewVisible).toBeFalse();
        } finally {
            jasmine.clock().uninstall();
        }
    });

    it("defaults automatic paging off for reduced motion and allows explicit opt-in", () => {
        spyOn(window, "matchMedia").and.returnValue({
            matches: true,
            addEventListener: jasmine.createSpy("addEventListener"),
            removeEventListener: jasmine.createSpy("removeEventListener")
        } as any);
        jasmine.clock().install();
        try {
            service.loadStatus.and.returnValue(of(status));
            createComponent();
            const component = fixture.componentInstance;

            jasmine.clock().tick(20000);
            expect(component.activeFeatureIndex).toBe(0);
            fixture.detectChanges();
            const images: NodeListOf<HTMLImageElement> = fixture.nativeElement.querySelectorAll(".feature-viewport img");
            expect(images[0].getAttribute("aria-hidden")).toBe("true");
            expect(images[1].classList).toContain("is-visible");
            expect(images[1].getAttribute("alt")).toContain("Movies, Series, Music, and Books");
            const autoPageInput: HTMLInputElement = fixture.nativeElement.querySelector(".auto-page-control input");
            expect(autoPageInput.checked).toBeFalse();

            const setupButton: HTMLButtonElement =
                fixture.nativeElement.querySelectorAll(".feature-image-controls button")[0];
            setupButton.click();
            fixture.detectChanges();
            expect(component.pathPairOverviewVisible).toBeFalse();
            jasmine.clock().tick(10000);
            expect(component.pathPairOverviewVisible).toBeFalse();

            component.setAutoAdvanceEnabled(true);
            jasmine.clock().tick(9999);
            expect(component.activeFeatureIndex).toBe(0);
            jasmine.clock().tick(1);
            expect(component.activeFeatureIndex).toBe(1);
        } finally {
            jasmine.clock().uninstall();
        }
    });
});
