import {CommonModule} from "@angular/common";
import {ChangeDetectorRef, Component, OnDestroy, OnInit} from "@angular/core";

import {MigrationFeature, MigrationStatus} from "../../services/migration/migration.model";
import {MigrationService} from "../../services/migration/migration.service";

declare function require(name: string): any;
const {version: appVersion} = require("../../../../package.json");

interface MigrationFeatureSlide extends MigrationFeature {
    imageSrc: string;
    imageAlt: string;
    imageTreatment: "contain" | "progress" | "claim" | "notifications" | "transfer" | "logs" | "queue";
    imageSelectorLabel?: string;
    secondaryImageSrc?: string;
    secondaryImageAlt?: string;
    secondaryImageSelectorLabel?: string;
    secondaryImageTreatment?: "overview";
}

const LARGE_QUEUE_FEATURE: MigrationFeature = {
    key: "large-queues",
    title: "Large libraries, your way",
    summary: "Show every download at once or choose 25, 50, 100, or 1,000 per page. Faster list updates, search, filters, sorting, and bulk actions keep big queues manageable."
};

const FEATURE_ORDER: Record<string, number> = {
    "path-pairs": 0,
    "accurate-progress": 1,
    "large-queues": 2,
    "secure-access": 3,
    "notifications": 4,
    "transfer-choices": 5,
    "historical-logs": 6
};

const FEATURE_IMAGES: Record<string, Pick<
    MigrationFeatureSlide,
    "imageSrc" | "imageAlt" | "imageTreatment" |
    "imageSelectorLabel" | "secondaryImageSrc" | "secondaryImageAlt" |
    "secondaryImageSelectorLabel" | "secondaryImageTreatment"
>> = {
    "path-pairs": {
        imageSrc: "/assets/migration/path-pairs.png",
        imageAlt: "SeedSync path-pair settings showing separate Series and Movies remote and local folders.",
        imageTreatment: "contain",
        imageSelectorLabel: "path-pair setup",
        secondaryImageSrc: "/assets/migration/path-pairs-overview.png",
        secondaryImageAlt: "SeedSync dashboard overview showing Movies, Series, Music, and Books path-pair categories together, including one active download.",
        secondaryImageSelectorLabel: "path-pair overview",
        secondaryImageTreatment: "overview"
    },
    "accurate-progress": {
        imageSrc: "/assets/migration/progress.png",
        imageAlt: "SeedSync dashboard showing active, stopped, local-only, and completed transfers with byte and percentage progress.",
        imageTreatment: "progress"
    },
    "secure-access": {
        imageSrc: "/assets/migration/first-claim.png",
        imageAlt: "SeedSync first-run browser page offering the Claim session action.",
        imageTreatment: "claim"
    },
    "notifications": {
        imageSrc: "/assets/migration/notifications.png",
        imageAlt: "SeedSync notification settings with provider, delivery event, save, and test controls.",
        imageTreatment: "notifications"
    },
    "transfer-choices": {
        imageSrc: "/assets/migration/current-settings.png",
        imageAlt: "SeedSync transfer settings showing rclone as the transfer backend and SFTP as the protocol.",
        imageTreatment: "transfer"
    },
    "historical-logs": {
        imageSrc: "/assets/migration/historical-logs.png",
        imageAlt: "SeedSync historical log search with text, severity, logger, and date range controls above redacted log entries.",
        imageTreatment: "logs"
    },
    "large-queues": {
        imageSrc: "/assets/migration/large-queues.png",
        imageAlt: "SeedSync files workspace with search, status and sort controls, selected bulk actions, file states, and pagination.",
        imageTreatment: "queue"
    }
};

@Component({
    selector: "app-root",
    standalone: true,
    imports: [CommonModule],
    templateUrl: "./migration-app.component.html",
    styleUrls: ["./migration-app.component.scss"]
})
export class MigrationAppComponent implements OnInit, OnDestroy {
    private static readonly FEATURE_ADVANCE_MS = 10000;
    private static readonly PATH_PAIR_IMAGE_INTERVAL_MS = 5000;

    status: MigrationStatus | null = null;
    loading = true;
    loadError = false;
    featureSlides: MigrationFeatureSlide[] = [];
    activeFeatureView: MigrationFeatureSlide[] = [];
    activeFeatureIndex = 0;
    autoAdvanceEnabled = true;

    private featureTimer: number | null = null;
    private featureImageTimer: number | null = null;
    private pathPairOverviewShown = false;
    private reducedMotion = false;
    private motionQuery: MediaQueryList | null = null;

    constructor(private readonly migrationService: MigrationService,
                private readonly changeDetector: ChangeDetectorRef) {}

    ngOnInit(): void {
        this.motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
        this.reducedMotion = this.motionQuery.matches;
        this.autoAdvanceEnabled = !this.reducedMotion;
        this.motionQuery.addEventListener("change", this.onMotionPreferenceChange);
        this.refresh();
    }

    ngOnDestroy(): void {
        this.clearFeatureTimer();
        this.motionQuery?.removeEventListener("change", this.onMotionPreferenceChange);
    }

    refresh(): void {
        this.clearFeatureTimer();
        this.loading = true;
        this.loadError = false;
        this.migrationService.loadStatus().subscribe({
            next: status => {
                this.status = status;
                this.featureSlides = this.buildFeatureSlides(status.features);
                this.activeFeatureIndex = 0;
                this.activeFeatureView = this.featureSlides.length ? [this.featureSlides[0]] : [];
                this.pathPairOverviewShown = this.reducedMotion;
                this.loading = false;
                this.loadError = false;
                this.resetFeatureTimer();
                this.changeDetector.markForCheck();
            },
            error: () => {
                this.status = null;
                this.featureSlides = [];
                this.activeFeatureView = [];
                this.activeFeatureIndex = 0;
                this.pathPairOverviewShown = false;
                this.loading = false;
                this.loadError = true;
                this.changeDetector.markForCheck();
            }
        });
    }

    get activeFeature(): MigrationFeatureSlide | null {
        return this.featureSlides[this.activeFeatureIndex] || null;
    }

    get sourceVersionLabel(): string {
        return "v0.8.6";
    }

    get isConfirmedV086Migration(): boolean {
        return this.status?.state === "required" &&
            this.status?.source_schema === "original-v0.8.6";
    }

    get targetVersionLabel(): string {
        return `v${appVersion}`;
    }

    get pathPairOverviewVisible(): boolean {
        return this.pathPairOverviewShown;
    }

    showFeature(index: number): void {
        this.setAutoAdvanceEnabled(false);
        this.activateFeature(index);
    }

    setAutoAdvanceEnabled(enabled: boolean): void {
        this.autoAdvanceEnabled = enabled;
        this.clearFeatureAdvanceTimer();
        this.scheduleFeatureAdvance();
        this.changeDetector.markForCheck();
    }

    showFeatureImage(index: number): void {
        if (this.activeFeature?.key !== "path-pairs" || !this.activeFeature.secondaryImageSrc ||
            (index !== 0 && index !== 1)) {
            return;
        }
        this.setAutoAdvanceEnabled(false);
        this.clearFeatureImageTimer();
        this.pathPairOverviewShown = index === 1;
        this.schedulePathPairImageToggle();
        this.changeDetector.markForCheck();
    }

    private activateFeature(index: number): void {
        if (index < 0 || index >= this.featureSlides.length || index === this.activeFeatureIndex) {
            return;
        }
        this.activeFeatureIndex = index;
        this.activeFeatureView = [this.featureSlides[index]];
        this.pathPairOverviewShown = this.reducedMotion && this.featureSlides[index].key === "path-pairs";
        this.resetFeatureTimer();
        this.changeDetector.markForCheck();
    }

    trackFeature(_: number, feature: MigrationFeatureSlide): string {
        return feature.key;
    }

    previousFeature(): void {
        if (this.featureSlides.length < 2) {
            return;
        }
        const index = (this.activeFeatureIndex - 1 + this.featureSlides.length) % this.featureSlides.length;
        this.showFeature(index);
    }

    nextFeature(): void {
        if (this.featureSlides.length < 2) {
            return;
        }
        this.showFeature((this.activeFeatureIndex + 1) % this.featureSlides.length);
    }

    get stateLabel(): string {
        switch (this.status?.state) {
            case "running": return "Migration state: running";
            case "failed": return "Migration readiness check failed";
            case "complete": return "Migration state: complete";
            case "required":
                return this.isConfirmedV086Migration ? "Migration required" : "Migration reassessment required";
            default: return "Migration status unavailable";
        }
    }

    get stateCopy(): string {
        switch (this.status?.state) {
            case "running":
                return "A migration was recorded as running. This checkpoint remains read-only while the state is reassessed.";
            case "failed":
                return "SeedSync could not complete the readiness check. No retry or migration operation is available from this page.";
            case "complete":
                return "Migration is recorded as complete. Recheck after the SeedSync service returns to normal startup.";
            case "required":
                if (!this.isConfirmedV086Migration) {
                    return "SeedSync could not confirm a supported migration source. No migration operation is available from this page.";
                }
                return "SeedSync found configuration from an earlier supported release. Your normal services are paused while migration safety is prepared.";
            default:
                return "SeedSync could not confirm the migration state. No migration operation is available from this page.";
        }
    }

    private buildFeatureSlides(features: MigrationFeature[]): MigrationFeatureSlide[] {
        const orderedFeatures = [...features, LARGE_QUEUE_FEATURE].sort((left, right) =>
            (FEATURE_ORDER[left.key] ?? Number.MAX_SAFE_INTEGER) -
            (FEATURE_ORDER[right.key] ?? Number.MAX_SAFE_INTEGER));
        return orderedFeatures.flatMap(feature => {
            const image = FEATURE_IMAGES[feature.key];
            return image ? [{...feature, ...image}] : [];
        });
    }

    private readonly onMotionPreferenceChange = (event: MediaQueryListEvent): void => {
        if (event.matches) {
            this.autoAdvanceEnabled = false;
            this.pathPairOverviewShown = true;
        }
        this.reducedMotion = event.matches;
        this.resetFeatureTimer();
        this.changeDetector.markForCheck();
    };

    private resetFeatureTimer(): void {
        this.clearFeatureTimer();
        this.schedulePathPairImageToggle();
        this.scheduleFeatureAdvance();
    }

    private schedulePathPairImageToggle(): void {
        if (this.reducedMotion || this.activeFeature?.key !== "path-pairs" ||
            this.featureImageTimer !== null) {
            return;
        }

        this.featureImageTimer = window.setTimeout(() => {
            this.featureImageTimer = null;
            if (this.reducedMotion || this.activeFeature?.key !== "path-pairs") {
                return;
            }
            this.pathPairOverviewShown = !this.pathPairOverviewShown;
            this.changeDetector.markForCheck();
            this.schedulePathPairImageToggle();
        }, MigrationAppComponent.PATH_PAIR_IMAGE_INTERVAL_MS);
    }

    private scheduleFeatureAdvance(): void {
        if (!this.autoAdvanceEnabled || this.featureSlides.length < 2) {
            return;
        }
        this.featureTimer = window.setTimeout(() => {
            this.featureTimer = null;
            this.activateFeature((this.activeFeatureIndex + 1) % this.featureSlides.length);
        }, MigrationAppComponent.FEATURE_ADVANCE_MS);
    }

    private clearFeatureTimer(): void {
        this.clearFeatureAdvanceTimer();
        this.clearFeatureImageTimer();
    }

    private clearFeatureImageTimer(): void {
        if (this.featureImageTimer !== null) {
            window.clearTimeout(this.featureImageTimer);
            this.featureImageTimer = null;
        }
    }

    private clearFeatureAdvanceTimer(): void {
        if (this.featureTimer !== null) {
            window.clearTimeout(this.featureTimer);
            this.featureTimer = null;
        }
    }
}
