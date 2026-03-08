import {AfterViewInit, Component, ElementRef, OnDestroy, OnInit, ViewChild} from "@angular/core";
import {NavigationEnd, Router} from "@angular/router";
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
    private _resizeObserver: any = null;

    constructor(private router: Router,
                private _domService: DomService) {
        // Navigation listener
        //    Close the sidebar
        //    Store the active route
        router.events.subscribe(() => {
            this.showSidebar = false;
            this.activeRoute = ROUTE_INFOS.find(value => "/" + value.path === router.url);
        });
    }

    ngOnInit() {
        // Scroll to top on route changes
        this.router.events.subscribe((evt) => {
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
    }

    title = "app";
}
