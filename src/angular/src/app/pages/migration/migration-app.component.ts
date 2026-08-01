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
    migrationConfirmed = false;
    actionBusy = false;
    actionError = false;
    normalStartupReleaseAccepted = false;

    private featureTimer: number | null = null;
    private featureImageTimer: number | null = null;
    private pathPairOverviewShown = false;
    private reducedMotion = false;
    private motionQuery: MediaQueryList | null = null;
    private statusPollTimer: number | null = null;
    private normalStartupProbeTimer: number | null = null;
    private normalStartupProbeAttempts = 0;
    private static readonly NORMAL_STARTUP_PROBE_MAX_ATTEMPTS = 120;

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
        this.clearStatusPoll();
        this.clearNormalStartupProbe();
        this.motionQuery?.removeEventListener("change", this.onMotionPreferenceChange);
    }

    refresh(polling = false): void {
        this.clearFeatureTimer();
        this.clearStatusPoll();
        this.loading = !polling;
        this.loadError = false;
        this.migrationService.loadStatus().subscribe({
            next: status => {
                this.status = status;
                if (!polling || !this.featureSlides.length) {
                    this.featureSlides = this.buildFeatureSlides(status.features);
                    this.activeFeatureIndex = 0;
                    this.activeFeatureView = this.featureSlides.length ? [this.featureSlides[0]] : [];
                    this.pathPairOverviewShown = this.reducedMotion;
                }
                this.loading = false;
                this.loadError = false;
                this.resetFeatureTimer();
                this.scheduleStatusPoll(status);
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

    get canStartMigration(): boolean {
        return !!this.status &&
            (this.status.capabilities.apply || this.status.capabilities.retry) &&
            (!this.status.capabilities.apply || this.isConfirmedV086Migration) &&
            this.migrationConfirmed && !this.actionBusy;
    }

    get actionLabel(): string {
        return this.status?.capabilities.retry ? "Retry migration" : "Start migration";
    }

    get canContinueToSeedSync(): boolean {
        return !!this.status && (this.status.capabilities.continue || this.normalStartupReleaseAccepted) &&
            !this.actionBusy;
    }

    get continueLabel(): string {
        if (this.actionBusy) {
            return "Starting SeedSync...";
        }
        return this.actionError ? "Retry checking SeedSync" : "Continue to SeedSync";
    }

    get readinessLabel(): string {
        if (this.status?.backup.complete_restore_ready) {
            return "Backup ready";
        }
        if (this.status?.operation.status === "running") {
            return "In progress";
        }
        return "Ready to start";
    }

    get readinessHeading(): string {
        if (this.status?.backup.complete_restore_ready ||
            this.status?.state === "complete" || this.status?.operation.status === "succeeded") {
            return "Retained backup ready";
        }
        if (this.status?.state === "running" || this.status?.operation.status === "running") {
            return "Creating and validating retained backup";
        }
        return "Complete retained backup before migration";
    }

    setMigrationConfirmed(confirmed: boolean): void {
        this.migrationConfirmed = confirmed;
        this.actionError = false;
    }

    startMigration(): void {
        if (!this.status || !this.canStartMigration) {
            return;
        }
        this.actionBusy = true;
        this.actionError = false;
        this.clearStatusPoll();
        this.migrationService.apply(this.status).subscribe({
            next: status => {
                this.status = status;
                this.actionBusy = false;
                this.migrationConfirmed = false;
                this.scheduleStatusPoll(status);
                this.changeDetector.markForCheck();
            },
            error: () => {
                this.actionBusy = false;
                this.actionError = true;
                this.scheduleStatusPoll(this.status as MigrationStatus);
                this.changeDetector.markForCheck();
            }
        });
    }

    continueToSeedSync(): void {
        if (!this.status || !this.canContinueToSeedSync) {
            return;
        }
        this.actionBusy = true;
        this.actionError = false;
        if (this.normalStartupReleaseAccepted) {
            this.beginNormalStartupProbe();
            this.changeDetector.markForCheck();
            return;
        }
        this.migrationService.continue(this.status).subscribe({
            next: () => {
                this.normalStartupReleaseAccepted = true;
                // The migration-only server now stops and the process loop
                // rebuilds the normal runtime. Do not navigate into a restart:
                // wait for the normal bootstrap route before changing pages.
                this.beginNormalStartupProbe();
                this.changeDetector.markForCheck();
            },
            error: () => {
                this.actionBusy = false;
                this.actionError = true;
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
        return this.status?.state === "required" && this.isV086MigrationPath;
    }

    get isV086MigrationPath(): boolean {
        return this.status?.migration_id === "original-v0.8.6-to-current-v1" &&
            this.status?.source_schema === "original-v0.8.6";
    }

    get isMigrationComplete(): boolean {
        return this.status?.state === "complete";
    }

    get isMigrationRunning(): boolean {
        return this.status?.state === "running" || this.status?.operation.status === "running";
    }

    get sourceVersionStateLabel(): string {
        return this.isMigrationComplete ? "Previous version" : "Detected source";
    }

    get targetVersionStateLabel(): string {
        return this.isMigrationComplete ? "Current version" : "Migration target";
    }

    get versionRouteAriaLabel(): string {
        if (this.isMigrationComplete) {
            return `Version transition complete: previous version ${this.sourceVersionLabel}; ` +
                `current version ${this.targetVersionLabel}.`;
        }
        return `Version transition: detected source ${this.sourceVersionLabel}; ` +
            `migration target ${this.targetVersionLabel}.`;
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
        if (this.isMigrationRunning) {
            return "Migration in progress";
        }
        switch (this.status?.state) {
            case "failed": return "Migration readiness check failed";
            case "complete": return "Migration complete";
            case "required":
                return this.isConfirmedV086Migration ? "Migration required" : "Migration reassessment required";
            default: return "Migration status unavailable";
        }
    }

    get stateCopy(): string {
        if (this.isMigrationRunning) {
            return "SeedSync is creating or validating the retained backup and applying the migration. Normal services remain paused.";
        }
        switch (this.status?.state) {
            case "failed":
                return this.status.retryable
                    ? "The migration stopped safely. The retained backup remains available and you can retry."
                    : "SeedSync could not complete the readiness check. Migration cannot be retried from this page.";
            case "complete":
                return "The migration and retained backup are ready. Continue when you are ready to start normal SeedSync and claim this browser.";
            case "required":
                if (!this.isConfirmedV086Migration) {
                    return "SeedSync could not confirm a supported migration source. No migration operation is available from this page.";
                }
                return "SeedSync found configuration from an earlier supported release. Your normal services remain paused until you confirm migration.";
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

    private scheduleStatusPoll(status: MigrationStatus): void {
        if (status.state !== "running" && status.operation.status !== "running") {
            return;
        }
        this.statusPollTimer = window.setTimeout(() => {
            this.statusPollTimer = null;
            this.refresh(true);
        }, 1000);
    }

    private beginNormalStartupProbe(): void {
        this.clearNormalStartupProbe();
        this.normalStartupProbeAttempts = 0;
        this.probeNormalStartup();
    }

    private probeNormalStartup(): void {
        this.migrationService.probeNormalStartup().subscribe({
            next: () => window.location.replace("/"),
            error: () => {
                this.normalStartupProbeAttempts += 1;
                if (this.normalStartupProbeAttempts >= MigrationAppComponent.NORMAL_STARTUP_PROBE_MAX_ATTEMPTS) {
                    this.normalStartupProbeTimer = null;
                    this.actionBusy = false;
                    this.actionError = true;
                    this.changeDetector.markForCheck();
                    return;
                }
                this.normalStartupProbeTimer = window.setTimeout(() => {
                    this.normalStartupProbeTimer = null;
                    this.probeNormalStartup();
                }, 250);
            }
        });
    }

    private clearNormalStartupProbe(): void {
        if (this.normalStartupProbeTimer !== null) {
            window.clearTimeout(this.normalStartupProbeTimer);
            this.normalStartupProbeTimer = null;
        }
        this.normalStartupProbeAttempts = 0;
    }

    private clearStatusPoll(): void {
        if (this.statusPollTimer !== null) {
            window.clearTimeout(this.statusPollTimer);
            this.statusPollTimer = null;
        }
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
