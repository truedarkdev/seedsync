import {CommonModule} from "@angular/common";
import {ComponentFixture, fakeAsync, TestBed, tick} from "@angular/core/testing";
import {FormsModule} from "@angular/forms";

import {OptionComponent, OptionType} from "../../../../pages/settings/option.component";


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
        component.label = "Log Level";
        component.value = "INFO";
        component.choices = [
            {label: "Debug", value: "DEBUG"},
            {label: "Info", value: "INFO"},
        ];
        component.changeEvent.subscribe(changeSpy);

        fixture.detectChanges();

        const select = fixture.nativeElement.querySelector("select") as HTMLSelectElement;
        expect(select.value).toBe("INFO");
        select.value = "DEBUG";
        select.dispatchEvent(new Event("change"));

        tick(1000);

        expect(changeSpy).toHaveBeenCalledWith("DEBUG");
    }));
});
