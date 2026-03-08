import {AfterViewInit, Component, ElementRef, OnDestroy, OnInit, ViewChild} from "@angular/core";
import {NavigationEnd, Router} from "@angular/router";
import {Subject} from "rxjs/Subject";
import "rxjs/add/operator/takeUntil";
import {ROUTE_INFOS, RouteInfo} from "../../routes";

import {DomService} from "../../services/utils/dom.service";

@Component({
    selector: "app-root",
    templateUrl: "./app.component.html",
    styleUrls: ["./app.component.scss"]
})
export class AppComponent implements OnInit, AfterViewInit, OnDestroy {
    @ViewChild("topHeader") topHeader: ElementRef;

    showSidebar = false;
    activeRoute: RouteInfo;
    private _destroy$: Subject<void> = new Subject<void>();
    private _resizeObserver: any = null;

    constructor(private router: Router,
                private _domService: DomService) {}

    ngOnInit() {
        // Navigation listener
        //    Close the sidebar
        //    Store the active route
        this.router.events.takeUntil(this._destroy$).subscribe(() => {
            this.showSidebar = false;
            this.activeRoute = ROUTE_INFOS.find(value => "/" + value.path === this.router.url);
        });

        // Scroll to top on route changes
        this.router.events.takeUntil(this._destroy$).subscribe((evt) => {
            if (!(evt instanceof NavigationEnd)) {
                return;
            }
            window.scrollTo(0, 0);
        });
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
}
