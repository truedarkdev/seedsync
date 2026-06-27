import {CommonModule} from "@angular/common";
import {ComponentFixture, fakeAsync, TestBed, tick} from "@angular/core/testing";
import {FormsModule} from "@angular/forms";

import {DEBOUNCE_TIME_MS, OptionComponent, OptionType} from "../../../../pages/settings/option.component";


describe("Testing option component", () => {
    let fixture: ComponentFixture<OptionComponent>;
    let component: OptionComponent;

    beforeEach(() => {
        TestBed.configureTestingModule({
            imports: [
                CommonModule,
                FormsModule
            ],
            declarations: [
                OptionComponent
            ]
        });

        fixture = TestBed.createComponent(OptionComponent);
        component = fixture.componentInstance;
    });

    it("should emit select changes", fakeAsync(() => {
        const changeSpy = jasmine.createSpy("change");
        component.type = OptionType.Select;
        component.label = "Log Format";
        component.value = "standard";
        component.choices = [
            {label: "Standard", value: "standard"},
            {label: "JSON", value: "json"},
        ];
        component.changeEvent.subscribe(changeSpy);

        fixture.detectChanges();

        const select = fixture.nativeElement.querySelector("select") as HTMLSelectElement;
        expect(select.value).toBe("standard");
        select.value = "json";
        select.dispatchEvent(new Event("change"));

        expect(changeSpy).not.toHaveBeenCalled();

        tick(DEBOUNCE_TIME_MS);

        expect(changeSpy).toHaveBeenCalledWith("json");
    }));

    it("should keep unknown select values visible as the current option", () => {
        component.type = OptionType.Select;
        component.label = "Log Format";
        component.value = "text";
        component.choices = [
            {label: "Standard", value: "standard"},
            {label: "JSON", value: "json"},
        ];

        fixture.detectChanges();

        const select = fixture.nativeElement.querySelector("select") as HTMLSelectElement;
        const optionLabels = Array.from(select.options).map(option => option.textContent.trim());

        expect(optionLabels[0]).toBe("text");
        expect(select.selectedIndex).toBe(0);
    });

    it("should emit the last value after the debounce window", fakeAsync(() => {
        const changeSpy = jasmine.createSpy("change");
        component.changeEvent.subscribe(changeSpy);

        fixture.detectChanges();

        component.onChange("first");
        tick(DEBOUNCE_TIME_MS - 1);
        component.onChange("second");
        tick(DEBOUNCE_TIME_MS);

        expect(changeSpy).toHaveBeenCalledTimes(1);
        expect(changeSpy).toHaveBeenCalledWith("second");
    }));

    it("should suppress duplicate values after debouncing", fakeAsync(() => {
        const changeSpy = jasmine.createSpy("change");
        component.changeEvent.subscribe(changeSpy);

        fixture.detectChanges();

        component.onChange("same");
        tick(DEBOUNCE_TIME_MS);
        component.onChange("same");
        tick(DEBOUNCE_TIME_MS);

        expect(changeSpy).toHaveBeenCalledTimes(1);
        expect(changeSpy).toHaveBeenCalledWith("same");
    }));

    it("should apply disabled styling when the disabled input is set", () => {
        component.type = OptionType.Select;
        component.label = "Transfer Protocol";
        component.value = "sftp";
        component.choices = [
            {label: "SFTP", value: "sftp"},
            {label: "FTPS", value: "ftps"},
        ];
        component.disabled = true;

        fixture.detectChanges();

        const formGroup = fixture.nativeElement.querySelector(".form-group") as HTMLElement;
        const select = fixture.nativeElement.querySelector("select") as HTMLSelectElement;

        expect(formGroup.classList.contains("disabled")).toBeTrue();
        expect(select.disabled).toBeTrue();
    });

    it("should keep null-valued selects visually normal when disabled is false", () => {
        component.type = OptionType.Select;
        component.label = "Transfer Protocol";
        component.value = null;
        component.choices = [
            {label: "SFTP", value: "sftp"},
            {label: "FTPS", value: "ftps"},
        ];
        component.disabled = false;

        fixture.detectChanges();

        const formGroup = fixture.nativeElement.querySelector(".form-group") as HTMLElement;
        const select = fixture.nativeElement.querySelector("select") as HTMLSelectElement;

        expect(formGroup.classList.contains("disabled")).toBeFalse();
        expect(select.disabled).toBeFalse();
    });
});
