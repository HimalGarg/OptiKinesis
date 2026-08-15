import cv2
import mediapipe as mp
import math

# Setup
mp_face_mesh = mp.solutions.face_mesh
face_mesh = mp_face_mesh.FaceMesh(refine_landmarks=True)
cap = cv2.VideoCapture(0)

# Landmarks (Left Eye)
LEFT_EYE = [33, 159, 133, 145] # [Left, Top, Right, Bottom]

def get_blink_ratio(landmarks, eye_indices):
    top = landmarks[eye_indices[1]]
    bottom = landmarks[eye_indices[3]]
    left = landmarks[eye_indices[0]]
    right = landmarks[eye_indices[2]]
    
    # Calculate distances
    ver_dist = math.hypot(top.x - bottom.x, top.y - bottom.y)
    hor_dist = math.hypot(left.x - right.x, left.y - right.y)
    
    return ver_dist / hor_dist

while True:
    ret, frame = cap.read()
    frame = cv2.flip(frame, 1)
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = face_mesh.process(rgb_frame)
    
    if results.multi_face_landmarks:
        landmarks = results.multi_face_landmarks[0].landmark
        
        # Calculate Ratio
        ratio = get_blink_ratio(landmarks, LEFT_EYE)
        
        # --- DEBUG DISPLAY ---
        # 1. Show the raw number large on screen
        cv2.putText(frame, f"Ratio: {round(ratio, 4)}", (50, 100), 
                    cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 255), 3)
        
        # 2. Visual test
        # Change 0.004 to whatever number you think is the limit
        limit = 0.2 
        if ratio < limit:
            cv2.putText(frame, "CLICK!", (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 255, 0), 3)

    cv2.imshow('Blink Debug', frame)
    if cv2.waitKey(1) & 0xFF == 27: break

cap.release()
cv2.destroyAllWindows()