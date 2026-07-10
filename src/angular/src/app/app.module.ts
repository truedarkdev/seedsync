import {APP_INITIALIZER, Provider} from "@angular/core";
import {RouteReuseStrategy} from "@angular/router";

import {environment} from "../environments/environment";
import {LoggerService} from "./services/utils/logger.service";
import {ViewFileService} from "./services/files/view-file.service";
import {ViewFileFilterService} from "./services/files/view-file-filter.service";
import {ViewFileSortService} from "./services/files/view-file-sort.service";
import {ViewFileOptionsService} from "./services/files/view-file-options.service";
import {FileSelectionService} from "./services/files/file-selection.service";
import {NotificationService} from "./services/utils/notification.service";
import {RestService} from "./services/utils/rest.service";
import {DomService} from "./services/utils/dom.service";
import {VersionCheckService} from "./services/utils/version-check.service";
import {Modal} from "./services/utils/modal.service";
import {ModalAccessibilityService} from "./services/utils/modal-accessibility.service";
import {StreamDispatchService, StreamServiceRegistryProvider} from "./services/base/stream-service.registry";
import {ServerStatusService} from "./services/server/server-status.service";
import {ModelFileService} from "./services/files/model-file.service";
import {ConnectedService} from "./services/utils/connected.service";
import {LogService} from "./services/logs/log.service";
import {AutoQueueService, AutoQueueServiceProvider} from "./services/autoqueue/autoqueue.service";
import {ConfigService, ConfigServiceProvider} from "./services/settings/config.service";
import {PathPairServiceProvider} from "./services/settings/path-pair.service";
import {ApiAccessServiceProvider} from "./services/settings/api-access.service";
import {ServerCommandServiceProvider} from "./services/server/server-command.service";
import {BulkCommandServiceProvider} from "./services/server/bulk-command.service";
import {CachedReuseStrategy} from "./common/cached-reuse-strategy";
import {STORAGE_SERVICE_PROVIDER} from "./services/utils/storage.service";

export function dummyFactory(_service: unknown) {
    return () => null;
}

export function loggerInitializer(logger: LoggerService) {
    return () => { logger.level = environment.logger.level; };
}

export const APP_PROVIDERS: Provider[] = [
    {provide: RouteReuseStrategy, useClass: CachedReuseStrategy},
    LoggerService,
    NotificationService,
    RestService,
    ViewFileService,
    ViewFileFilterService,
    ViewFileSortService,
    ViewFileOptionsService,
    FileSelectionService,
    DomService,
    VersionCheckService,
    Modal,
    ModalAccessibilityService,
    StreamDispatchService,
    StreamServiceRegistryProvider,
    ServerStatusService,
    ModelFileService,
    ConnectedService,
    LogService,
    AutoQueueServiceProvider,
    ConfigServiceProvider,
    PathPairServiceProvider,
    ApiAccessServiceProvider,
    ServerCommandServiceProvider,
    BulkCommandServiceProvider,
    STORAGE_SERVICE_PROVIDER,
    {provide: APP_INITIALIZER, useFactory: loggerInitializer, deps: [LoggerService], multi: true},
    {provide: APP_INITIALIZER, useFactory: dummyFactory, deps: [ViewFileFilterService], multi: true},
    {provide: APP_INITIALIZER, useFactory: dummyFactory, deps: [ViewFileSortService], multi: true},
    {provide: APP_INITIALIZER, useFactory: dummyFactory, deps: [VersionCheckService], multi: true},
    {provide: APP_INITIALIZER, useFactory: dummyFactory, deps: [ConfigService], multi: true},
    {provide: APP_INITIALIZER, useFactory: dummyFactory, deps: [AutoQueueService], multi: true},
];
