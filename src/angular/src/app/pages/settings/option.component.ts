import {Component, Input, Output, ChangeDetectionStrategy, EventEmitter, OnInit} from "@angular/core";
import {Subject} from "rxjs";
import {debounceTime, distinctUntilChanged} from "rxjs/operators";

export const DEBOUNCE_TIME_MS = 1000;

@Component({
    selector: "app-option",
    standalone: false,
    providers: [],
    templateUrl: "./option.component.html",
    styleUrls: ["./option.component.scss"],
    changeDetection: ChangeDetectionStrategy.OnPush
})

export class OptionComponent implements OnInit {
    @Input() type: OptionType;
    @Input() label: string;
    @Input() value: any;
    @Input() description: string | null;
    @Input() choices: IOptionChoice[];
    @Input() disabled = false;

    @Output() changeEvent = new EventEmitter<any>();

    // expose to template
    public OptionType = OptionType;

    private newValue = new Subject<any>();

    get effectiveChoices(): IOptionChoice[] {
        const choices = this.choices || [];
        const currentValue = this.value;
        if (typeof currentValue === "string" && currentValue.length > 0 && !choices.some(choice => choice.value === currentValue)) {
            return [{label: currentValue, value: currentValue}, ...choices];
        }
        return choices;
    }

    get isDisabled(): boolean {
        return this.disabled;
    }

    // noinspection JSUnusedGlobalSymbols
    ngOnInit(): void {
        // Debounce
        // References:
        //      https://angular.io/tutorial/toh-pt6#fix-the-herosearchcomponent-class
        //      https://stackoverflow.com/a/41965515
        this.newValue.pipe(
            debounceTime(DEBOUNCE_TIME_MS),
            distinctUntilChanged()
        )
            .subscribe({next: val => this.changeEvent.emit(val)});
    }

    onChange(value: any) {
        this.newValue.next(value);
    }
}

export enum OptionType {
    Text,
    Checkbox,
    Password,
    Select
}

export interface IOptionChoice {
    label: string;
    value: any;
}
