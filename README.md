=LET(data,TOCOL('Original Flow'!A:XFD,1),FILTER(data,COUNTIF('[007.2_Method_Detailed_Flow_Occurrence_Distribution_reorder_1.xlsx]Original Flow'!A:XFD,data)=0,"No missing values"))
