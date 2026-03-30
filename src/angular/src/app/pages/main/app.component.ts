import {AfterViewInit, Component, ElementRef, OnDestroy, OnInit, ViewChild} from "@angular/core";
import {NavigationEnd, Router} from "@angular/router";
import {Subject} from "rxjs/Subject";
import "rxjs/add/operator/takeUntil";
import {ROUTE_INFOS} from "../../routes";

import {DomService} from "../../services/utils/dom.service";
import {PathPair, PathPairService} from "../../services/settings/path-pair.service";
import {resolvePathPairRouteSegment} from "../../services/settings/path-pair-route";

@Component({
    selector: "app-root",
    templateUrl: "./app.component.html",
    styleUrls: ["./app.component.scss"]
})
export class AppComponent implements OnInit, AfterViewInit, OnDestroy {
    @ViewChild("topHeader") topHeader: ElementRef;

    showSidebar = false;
    activeTitle = "";
    private _destroy$: Subject<void> = new Subject<void>();
    private _resizeObserver: any = null;
    private _pathPairs: PathPair[] = [];

    constructor(private router: Router,
                private _pathPairService: PathPairService,
                private _domService: DomService) {}

    ngOnInit() {
        this._pathPairService.pathPairs.takeUntil(this._destroy$).subscribe({
            next: (pathPairs: PathPair[]) => {
                this._pathPairs = pathPairs || [];
                this._updateActiveTitle();
            }
        });

        // Navigation listener
        //    Close the sidebar
        //    Store the active route
        this.router.events.takeUntil(this._destroy$).subscribe((evt) => {
            this.showSidebar = false;
            if (evt instanceof NavigationEnd) {
                this._updateActiveTitle();
            }
        });

        // Scroll to top on route changes
        this.router.events.takeUntil(this._destroy$).subscribe((evt) => {
            if (!(evt instanceof NavigationEnd)) {
                return;
            }
            window.scrollTo(0, 0);
        });

        this._updateActiveTitle();
    }

    ngAfterViewInit() {
        this._domService.setHeaderHeight(this.topHeader.nativeElement.clientHeight);
        const ResizeObserverCtor = (window as any).ResizeObserver;
        if (!ResizeObserverCtor) {
            return;
        }

        this._resizeObserver = new ResizeObserverCtor(() => {
            this._domService.setHeaderHeight(this.topHeader.nativeElement.clientHeight);
        });
        this._resizeObserver.observe(this.topHeader.nativeElement);
    }

    ngOnDestroy() {
        if (this._resizeObserver) {
            this._resizeObserver.disconnect();
        }
        this._destroy$.next();
        this._destroy$.complete();
    }

    title = "app";

    private _updateActiveTitle(): void {
        const currentUrl = (this.router.url || "").split(/[?#]/)[0];
        const routeInfo = ROUTE_INFOS.find(value => `/${value.path}` === currentUrl);
        if (routeInfo != null) {
            this.activeTitle = routeInfo.name;
            return;
        }

        const pathPairMatch = currentUrl.match(/^\/dashboard\/([^/]+)$/);
        if (pathPairMatch != null && this._hasMultipleEnabledPathPairs()) {
            const enabledPathPairs = this._pathPairs.filter(pair => pair.enabled);
            const pathPairRouteMatch = resolvePathPairRouteSegment(pathPairMatch[1], enabledPathPairs);
            if (pathPairRouteMatch.type === "id" || pathPairRouteMatch.type === "slug") {
                this.activeTitle = pathPairRouteMatch.pathPair.name;
            } else {
                this.activeTitle = "Dashboard";
            }
            return;
        }

        this.activeTitle = currentUrl.indexOf("/dashboard") === 0 ? "Dashboard" : "";
    }

    private _hasMultipleEnabledPathPairs(): boolean {
        return this._pathPairs.filter(pair => pair.enabled).length > 1;
    }

}
