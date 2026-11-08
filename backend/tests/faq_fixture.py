"""A synthetic 60-entry clinic FAQ (English, Hindi, Marathi, Hinglish) and 30 paraphrased caller questions, each with the
entry that answers it. Used by tests/test_faq_answering.py and the measuring script; invented facts only."""

# (key, question, answer). The key is unique and is how a test says which entry should come first.
ENTRIES = [
    # ---- English
    ("en_hours", "What are the clinic opening hours?", "The clinic is open Monday to Saturday from 10 am to 1 pm and 5 pm to 8 pm. Sunday is closed."),
    ("en_fee", "How much is the consultation fee for a general physician?", "The general physician consultation fee is 500 rupees. A follow-up within seven days is free."),
    ("en_parking", "Is there parking available at the clinic?", "Yes, free parking for twenty cars is behind the building. Two wheelers park near the gate."),
    ("en_xray", "Do you have an X-ray facility?", "Yes, digital X-ray is done on the ground floor between 11 am and 6 pm. A report is ready in thirty minutes."),
    ("en_bloodtest", "Can I get blood tests done at the clinic?", "Blood samples are collected every morning from 8 am to 10 am. Fasting is needed for sugar and cholesterol tests."),
    ("en_insurance", "Which health insurance companies do you accept for cashless treatment?", "We accept cashless claims from Star Health, HDFC Ergo and Niva Bupa. Please bring your insurance card and a photo ID."),
    ("en_cancel", "What is the cancellation policy for appointments?", "You can cancel or move an appointment up to two hours before the time without any charge."),
    ("en_pediatric", "Is there a child specialist who sees babies and young children?", "Dr. Rohan Joshi is our paediatrician. He sees children on Tuesday, Thursday and Saturday from 5 pm to 8 pm."),
    ("en_dental", "Do you offer dental check ups and teeth cleaning?", "The dental clinic is open on Monday, Wednesday and Friday. Scaling and polishing costs 1200 rupees."),
    ("en_report", "How can I collect my lab reports?", "Reports are sent on WhatsApp the same evening. You can also collect printed copies from the reception desk."),
    ("en_vaccine", "Are vaccinations given for children and adults?", "Yes, we give routine child vaccines and flu, typhoid and hepatitis B shots for adults. Please book a slot in advance."),
    ("en_emergency", "What should I do in a medical emergency at night?", "The clinic does not run overnight. For an emergency please call 108 or go to the nearest hospital casualty."),
    ("en_pregnancy", "Do you have a gynaecologist for pregnancy check ups?", "Dr. Anjali Kulkarni sees antenatal patients on Monday, Wednesday and Friday from 10 am to 1 pm."),
    ("en_payment", "Which payment methods are accepted at the counter?", "We accept cash, UPI, debit cards and credit cards. There is no extra charge for card payments."),
    ("en_walkin", "Can I walk in without an appointment?", "Walk-ins are welcome, but patients with an appointment are seen first. The usual wait is about twenty minutes."),
    # ---- Hindi (Devanagari)
    ("hi_ecg", "क्या क्लिनिक में ईसीजी की सुविधा है?", "जी हाँ, ईसीजी सुबह दस बजे से दोपहर एक बजे तक होती है। इसका शुल्क तीन सौ रुपये है।"),
    ("hi_physio", "फिजियोथेरेपी का समय क्या है?", "फिजियोथेरेपी सोमवार से शनिवार शाम पाँच बजे से सात बजे तक होती है। एक सत्र का शुल्क सात सौ रुपये है।"),
    ("hi_sugar", "शुगर की जाँच के लिए क्या खाली पेट आना ज़रूरी है?", "हाँ, शुगर की फास्टिंग जाँच के लिए आठ घंटे खाली पेट रहना ज़रूरी है। सिर्फ पानी पी सकते हैं।"),
    ("hi_skin", "त्वचा रोग विशेषज्ञ कब बैठते हैं?", "डॉक्टर नेहा वर्मा त्वचा रोग विशेषज्ञ हैं। वे मंगलवार और शुक्रवार को दोपहर बारह बजे तक बैठती हैं।"),
    ("hi_eye", "आँखों की जाँच और चश्मे का नंबर कहाँ होता है?", "आँखों की जाँच पहली मंजिल पर होती है। चश्मे के नंबर की जाँच का शुल्क दो सौ रुपये है।"),
    ("hi_medicine", "क्या क्लिनिक में दवाई की दुकान है?", "जी हाँ, क्लिनिक के अंदर ही मेडिकल स्टोर है। वह सुबह नौ बजे से रात आठ बजे तक खुला रहता है।"),
    ("hi_home", "क्या डॉक्टर घर आकर देखते हैं?", "घर पर विज़िट सिर्फ बुज़ुर्ग मरीज़ों के लिए है। इसका शुल्क आठ सौ रुपये है और पहले से बुकिंग ज़रूरी है।"),
    ("hi_certificate", "मेडिकल सर्टिफिकेट कैसे मिलेगा?", "मेडिकल सर्टिफिकेट जाँच के बाद उसी दिन मिल जाता है। इसका शुल्क सौ रुपये है।"),
    ("hi_ac", "क्या वेटिंग रूम में बैठने और पीने के पानी की व्यवस्था है?", "जी हाँ, वेटिंग रूम में वातानुकूलित बैठक और साफ़ पीने का पानी उपलब्ध है।"),
    ("hi_bp", "ब्लड प्रेशर की जाँच का शुल्क कितना है?", "ब्लड प्रेशर की जाँच मुफ़्त है। यह रिसेप्शन पर नर्स करती हैं।"),
    ("hi_ortho", "हड्डी के डॉक्टर कौन से दिन आते हैं?", "हड्डी रोग विशेषज्ञ डॉक्टर अमित देशमुख बुधवार और शनिवार को शाम छह बजे से आठ बजे तक आते हैं।"),
    ("hi_diet", "क्या डाइट प्लान के लिए पोषण विशेषज्ञ मिल सकते हैं?", "पोषण विशेषज्ञ गुरुवार को सुबह ग्यारह बजे से दोपहर दो बजे तक मिलती हैं। परामर्श का शुल्क पाँच सौ रुपये है।"),
    ("hi_senior", "बुज़ुर्ग मरीज़ों के लिए कोई छूट है?", "साठ साल से ऊपर के मरीज़ों को परामर्श शुल्क में बीस प्रतिशत की छूट मिलती है।"),
    ("hi_ultrasound", "सोनोग्राफी कब और कहाँ होती है?", "सोनोग्राफी भूतल पर रोज़ सुबह ग्यारह बजे से शाम चार बजे तक होती है। शुल्क नौ सौ रुपये है।"),
    ("hi_address", "क्लिनिक का पता क्या है और कहाँ है?", "क्लिनिक सनराइज़ बिल्डिंग, गणेश रोड, पुणे में है। पास में पुराना बस स्टैंड और मंदिर है।"),
    # ---- Marathi
    ("mr_timing", "दवाखाना रविवारी उघडा असतो का?", "दवाखाना रविवारी बंद असतो. सोमवार ते शनिवार सकाळी दहा ते दुपारी एक आणि संध्याकाळी पाच ते आठ उघडा असतो."),
    ("mr_fee", "लहान मुलांच्या डॉक्टरांची फी किती आहे?", "बालरोगतज्ज्ञांची फी चारशे रुपये आहे. सात दिवसांत पुन्हा दाखवल्यास फी लागत नाही."),
    ("mr_appointment", "अपॉइंटमेंट कशी घ्यावी?", "अपॉइंटमेंट फोनवर किंवा व्हॉट्सअॅपवर घेता येते. आम्ही तारीख आणि वेळ पुन्हा वाचून दाखवतो."),
    ("mr_xray", "एक्स-रे काढण्याची सोय आहे का?", "होय, एक्स-रे तळमजल्यावर सकाळी अकरा ते संध्याकाळी सहा या वेळेत काढला जातो. रिपोर्ट अर्ध्या तासात मिळतो."),
    ("mr_blood", "रक्ताची तपासणी कधी करता येते?", "रक्ताचे नमुने रोज सकाळी आठ ते दहा या वेळेत घेतले जातात. साखरेच्या तपासणीसाठी उपाशी राहावे लागते."),
    ("mr_parking", "गाडी लावायला जागा आहे का?", "होय, इमारतीच्या मागे वीस गाड्यांसाठी मोफत पार्किंग आहे. दुचाकी गेटजवळ लावता येतात."),
    ("mr_insurance", "आरोग्य विमा चालतो का?", "होय, स्टार हेल्थ आणि एचडीएफसी एर्गो यांचा कॅशलेस विमा चालतो. विमा कार्ड आणि ओळखपत्र आणावे."),
    ("mr_dentist", "दातांचे डॉक्टर कोणत्या दिवशी येतात?", "दंतवैद्य सोमवार, बुधवार आणि शुक्रवारी येतात. दात स्वच्छ करण्याचे बाराशे रुपये लागतात."),
    ("mr_report", "तपासणीचा रिपोर्ट कसा मिळेल?", "रिपोर्ट त्याच दिवशी संध्याकाळी व्हॉट्सअॅपवर पाठवला जातो. छापील प्रत रिसेप्शनवर मिळते."),
    ("mr_sonography", "सोनोग्राफीची वेळ काय आहे?", "सोनोग्राफी रोज सकाळी अकरा ते दुपारी चार या वेळेत केली जाते. शुल्क नऊशे रुपये आहे."),
    ("mr_cancel", "अपॉइंटमेंट रद्द करता येते का?", "होय, वेळेच्या दोन तास आधीपर्यंत अपॉइंटमेंट रद्द किंवा बदलता येते. त्यासाठी कोणतेही शुल्क नाही."),
    ("mr_pregnancy", "गर्भवती महिलांसाठी स्त्रीरोगतज्ज्ञ आहेत का?", "डॉक्टर अंजली कुलकर्णी सोमवार, बुधवार आणि शुक्रवारी सकाळी दहा ते एक गरोदर महिलांना तपासतात."),
    ("mr_vaccine", "मुलांना लसीकरण केले जाते का?", "होय, मुलांची नियमित लसीकरणे आणि मोठ्यांसाठी फ्लूची लस दिली जाते. आधी वेळ घ्यावी."),
    ("mr_payment", "पैसे कसे भरता येतात?", "रोख, यूपीआय आणि कार्डने पैसे भरता येतात. कार्डसाठी जास्तीचे शुल्क नाही."),
    ("mr_emergency", "रात्री आणीबाणी असेल तर काय करावे?", "रात्री दवाखाना बंद असतो. आणीबाणीत एकशे आठ क्रमांकावर फोन करा किंवा जवळच्या रुग्णालयात जा."),
    # ---- Hinglish (Roman script)
    ("hg_checkup", "Full body checkup ka package kitne ka hai?", "Full body checkup package 2500 rupees ka hai. Isme blood test, ECG aur X-ray shamil hain."),
    ("hg_timing", "Doctor sahab shaam ko kitne baje tak baithte hain?", "Doctor shaam ko saat baje se aath baje tak baithte hain. Aakhri token saade saat baje milta hai."),
    ("hg_thyroid", "Thyroid test ke liye kya khali pet aana padta hai?", "Nahi, thyroid test ke liye khali pet aana zaroori nahi hai. Subah ya shaam kabhi bhi ho sakta hai."),
    ("hg_report_wait", "Blood report kitne ghante mein aa jaati hai?", "Blood report usi din shaam tak aa jaati hai. WhatsApp par bhej dete hain."),
    ("hg_fever", "Bachche ko bukhar hai toh abhi kya karein?", "Bukhar ho toh turant clinic aaiye ya phone par token le lijiye. Paracetamol sirf doctor ki salah se dein."),
    ("hg_discount", "Kya family package par koi discount milta hai?", "Haan, chaar ya usse zyada log ek saath aayen toh consultation fee par dus percent discount milta hai."),
    ("hg_refund", "Agar doctor nahi aaye toh paise wapas milenge kya?", "Agar doctor available nahi hue toh poori fee wapas ki jaati hai ya appointment free mein badal di jaati hai."),
    ("hg_online", "Online video consultation ho sakti hai kya?", "Haan, video consultation hoti hai. Fee 400 rupees hai aur link WhatsApp par bheja jaata hai."),
    ("hg_prescription", "Purani prescription ka refill kaise milega?", "Purani prescription ke liye doctor se phone par baat karwa dete hain. Refill ke liye clinic aana zaroori nahi hai."),
    ("hg_covid", "Mask lagana zaroori hai kya clinic mein?", "Mask zaroori nahi hai, lekin khaansi ya bukhar wale mareez mask lagayein."),
    ("hg_gst", "Bill mein GST lagta hai kya?", "Medical consultation par GST nahi lagta. Dawai aur kuch tests par lagu hota hai."),
    ("hg_token", "Token system kaise kaam karta hai?", "Aap phone par token book karte hain. Clinic pahunchne se pehle SMS aata hai jisme aapka number hota hai."),
    ("hg_lift", "Kya clinic mein lift hai, dadaji seedhi nahi chadh sakte?", "Haan, building mein lift hai aur wheelchair ki bhi suvidha hai."),
    ("hg_physio_sessions", "Physiotherapy ke liye kitne session lagte hain?", "Zyadatar mareezon ko 6 se 10 session lagte hain. Doctor pehli visit mein bata dete hain."),
    ("hg_language", "Kya doctor Marathi ya Hindi mein baat karte hain?", "Haan, sabhi doctor Hindi, Marathi aur English mein baat karte hain."),
]

# (caller question, key of the entry that answers it): paraphrases, other scripts and code-mixed asks, none a copy of the
# stored question.
QUERIES = [
    ("what time do you open and close", "en_hours"),
    ("kitne baje khulta hai clinic", "en_hours"),
    ("doctor ki fees kitni hai general physician ki", "en_fee"),
    ("can I park my car there", "en_parking"),
    ("gaadi parking milegi kya", "en_parking"),
    ("do you do x ray here", "en_xray"),
    ("which insurance do you take, cashless chalta hai kya", "en_insurance"),
    ("can I change my appointment time later", "en_cancel"),
    ("बच्चों के डॉक्टर कब आते हैं", None),            # no Hindi paediatrician entry: only the English one fits
    ("kids ka doctor kab baithta hai", "en_pediatric"),
    ("can I pay by UPI", "en_payment"),
    ("without appointment aa sakta hu kya", "en_walkin"),
    ("ईसीजी कितने का है", "hi_ecg"),
    ("physiotherapy kab hoti hai", "hi_physio"),
    ("sugar test ke liye bhookha rehna padega kya", "hi_sugar"),
    ("चश्मे का नंबर चेक होता है क्या", "hi_eye"),
    ("clinic mein medical store hai kya", "hi_medicine"),
    ("बुज़ुर्गों को डिस्काउंट मिलता है", "hi_senior"),
    ("sonography ka time aur fees batao", "hi_ultrasound"),
    ("दवाखाना रविवारी चालू असतो का", "mr_timing"),
    ("बाळाच्या डॉक्टरांची फी सांगा", "mr_fee"),
    ("दात साफ करायला किती पैसे", "mr_dentist"),
    ("रात्री इमर्जन्सी असेल तर काय", "mr_emergency"),
    ("report whatsapp pe aayega kya", "mr_report"),
    ("full body check up ka rate kya hai", "hg_checkup"),
    ("thyroid ke test mein kuch khana mana hai kya", "hg_thyroid"),
    ("video call pe doctor se baat ho sakti hai", "hg_online"),
    ("GST lagega kya bill pe", "hg_gst"),
    ("lift hai kya building mein", "hg_lift"),
    ("doctor Marathi bolte hain kya", "hg_language"),
]
QUERIES = [(q, k) for q, k in QUERIES if k]


def faq_text() -> str:
    """The FAQ as a plain text document the way a clinic would upload it: one 'Q:' / 'A:' pair per entry under a title."""
    lines = ["# Sunrise Family Clinic: frequently asked questions", ""]
    for _key, q, a in ENTRIES:
        lines += [f"Q: {q}", f"A: {a}", ""]
    return "\n".join(lines)


# Entries that state the same fact in another language: either answers a caller who asks it in any language.
_SAME_FACT = [
    {"en_hours", "mr_timing"}, {"en_parking", "mr_parking"}, {"en_xray", "mr_xray"}, {"en_bloodtest", "mr_blood"},
    {"en_insurance", "mr_insurance"}, {"en_cancel", "mr_cancel"}, {"en_dental", "mr_dentist"},
    {"en_report", "mr_report", "hg_report_wait"}, {"en_vaccine", "mr_vaccine"}, {"en_emergency", "mr_emergency"},
    {"en_pregnancy", "mr_pregnancy"}, {"en_payment", "mr_payment"}, {"hi_ultrasound", "mr_sonography"},
    {"hi_physio", "hg_physio_sessions"},
]


def accepted_keys(key: str) -> set:
    """The entry that was asked for and every entry that gives the same answer in another language."""
    out = {key}
    for group in _SAME_FACT:
        if key in group:
            out |= group
    return out


def key_of_passage(passage: str) -> str:
    """Key of the FAQ entry a retrieved passage text holds ('' when it holds none)."""
    for key, q, _a in ENTRIES:
        if f"Q: {q}" in passage:
            return key
    return ""
