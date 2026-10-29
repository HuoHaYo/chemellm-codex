"""UI examples, explicitly assumptions. Never automatically chosen by model execution."""
EXAMPLES={
    "cantera_equilibrium":{"mechanism":"wgs4","constraint":"TP","temperature_K":1000,"pressure_Pa":101325,"composition":{"CO":1,"H2O":1,"CO2":0.1,"H2":0.1}},
    "fit_data":{"x":[1,2,3,5,7,10],"y":[109,149,149,191,213,224],"model":"first_order_uptake","x_unit":"day","y_unit":"mg/liter"},
    "equipment_save":{"record":{"tag":"E-101","name":"原料预热器","equipment_type":"exchanger","material":"","description":"示例输入；请替换为本项目设计依据。","parameters":[{"name":"area","label":"换热面积","value":100,"unit":"m^2","provenance":"assumption"}]}},
    "pinch_analysis":{"streams":[
        {"name":"H1","kind":"hot","supply_C":180,"target_C":40,"heat_capacity_flow_kW_K":2},
        {"name":"C1","kind":"cold","supply_C":20,"target_C":160,"heat_capacity_flow_kW_K":1}
    ]}
}
