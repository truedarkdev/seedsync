import {Record} from "immutable";

import {ViewFile} from "./view-file";

/**
 * View file options
 * Describes display related options for view files
 */
interface IViewFileOptions {
    // Show additional details about the view file
    showDetails: boolean;

    // Method to use to sort the view file list
    sortMethod: ViewFileOptions.SortMethod;

    // Status filter setting
    selectedStatusFilter: ViewFile.Status;

    // Name filter setting
    nameFilter: string;

    // Track filter pin status
    pinFilter: boolean;
}


// Boiler plate code to set up an immutable class
const DefaultViewFileOptions: IViewFileOptions = {
    showDetails: null,
    sortMethod: null,
    selectedStatusFilter: null,
    nameFilter: null,
    pinFilter: null,
};
const ViewFileOptionsRecord = Record(DefaultViewFileOptions);


/**
 * Immutable class that implements the interface
 */
export class ViewFileOptions extends ViewFileOptionsRecord implements IViewFileOptions {
    showDetails: boolean;
    sortMethod: ViewFileOptions.SortMethod;
    selectedStatusFilter: ViewFile.Status;
    nameFilter: string;
    pinFilter: boolean;

    constructor(props) {
        super(props);
    }
}

export module ViewFileOptions {
    export enum SortMethod {
        SMART_STATUS = 10,
        STATUS = 0,
        STATUS_DESC = 3,
        NAME_ASC = 1,
        NAME_DESC = 2,
        SIZE_ASC = 4,
        SIZE_DESC = 5,
        SPEED_ASC = 6,
        SPEED_DESC = 7,
        ETA_ASC = 8,
        ETA_DESC = 9
    }
}
