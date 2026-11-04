/** A short line to hear a voice say: in its own language when there is a sample for it, else English. */
const SAMPLES: Record<string, string> = {
  hi: "नमस्ते, आपका स्वागत है। आपकी अपॉइंटमेंट कल सुबह दस बजे के लिए पक्की है।",
  mr: "नमस्कार, तुमचे स्वागत आहे. तुमची भेट उद्या सकाळी दहा वाजता नक्की झाली आहे.",
  ta: "வணக்கம், உங்களை வரவேற்கிறோம். உங்கள் சந்திப்பு நாளை காலை பத்து மணிக்கு உறுதி செய்யப்பட்டது.",
  te: "నమస్కారం, మీకు స్వాగతం. మీ అపాయింట్‌మెంట్ రేపు ఉదయం పది గంటలకు ఖరారైంది.",
  bn: "নমস্কার, আপনাকে স্বাগত। আপনার অ্যাপয়েন্টমেন্ট আগামীকাল সকাল দশটায় নিশ্চিত হয়েছে।",
};

export function previewLine(language: string | undefined | null): string {
  return SAMPLES[(language ?? "").toLowerCase()] ?? "Hello, this is how my voice sounds. Your appointment is confirmed for tomorrow morning.";
}
