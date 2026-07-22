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
        schema_version: 1,
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
        capabilities: {apply: false, retry: false, restore: false},
        backup: {required: true, complete_restore_ready: false, status: "not_ready"},
        blocker: "complete_backup_restore_not_ready"
    };

    beforeEach(() => {
        service = jasmine.createSpyObj<MigrationService>("MigrationService", ["loadStatus"]);
        TestBed.configureTestingModule({
            imports: [MigrationAppComponent],
            providers: [{provide: MigrationService, useValue: service}]
        });
    });

    function createComponent(): void {
        fixture = TestBed.createComponent(MigrationAppComponent);
        fixture.detectChanges();
    }

    it("renders an asynchronously fetched required state and disabled migration action", async () => {
        const response = new Subject<MigrationStatus>();
        service.loadStatus.and.returnValue(response.asObservable());
        createComponent();
        fixture.autoDetectChanges();

        expect(fixture.nativeElement.textContent).toContain("Checking migration status");

        TestBed.get(NgZone).runOutsideAngular(() => response.next(status));
        await fixture.whenStable();

        const text = fixture.nativeElement.textContent;
        const startButton: HTMLButtonElement = fixture.nativeElement.querySelector(".primary-button");
        expect(text).toContain("v0.8.6");
        expect(text).toContain("v0.9.0");
        expect(text).not.toContain("original-v0.8.6");
        expect(text).toContain("Complete backup and restore required");
        expect(text).toContain("This build does not provide that capability yet");
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

    it("renders failed state without enabling retry", () => {
        service.loadStatus.and.returnValue(of({
            ...status,
            state: "failed",
            retryable: true,
            error: {code: "migration_preflight_failed", message: "Readiness check failed."}
        }));
        createComponent();

        expect(fixture.nativeElement.textContent).toContain("Migration readiness check failed");
        expect(fixture.nativeElement.textContent).toContain("No retry or migration operation is available");
        expect(fixture.nativeElement.textContent).not.toContain("v0.8.6");
        expect(fixture.nativeElement.querySelector(".migration-route")).toBeNull();
        expect(fixture.nativeElement.querySelector(".primary-button")).toBeNull();
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

    it("renders running and complete states as recheck-only", () => {
        for (const stateName of ["running", "complete"] as const) {
            service.loadStatus.and.returnValue(of({...status, state: stateName}));
            createComponent();

            expect(fixture.nativeElement.textContent).toContain(`Migration state: ${stateName}`);
            expect(fixture.nativeElement.querySelector(".primary-button")).toBeNull();
            expect(fixture.nativeElement.querySelector(".secondary-button").textContent).toContain("Recheck status");
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
