// The file contents for the current environment will overwrite these during build.
// The build system defaults to the dev environment which uses `environment.ts`, but if you do
// `ng build --env=prod` then `environment.prod.ts` will be used instead.
// The list of which env maps to which file is defined in `angular.json`.

import {LoggerService} from "../app/services/utils/logger.service"

export const environment = {
    production: false,
    logger: {
        level: LoggerService.Level.DEBUG
    }
};
