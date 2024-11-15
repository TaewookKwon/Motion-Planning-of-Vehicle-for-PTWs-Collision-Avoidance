#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import rospy
import numpy as np
from std_msgs.msg import Int32, Float32
from geometry_msgs.msg import Point32,PoseStamped
from sensor_msgs.msg import Imu
from beginner_tutorials.msg import TrajectoryArray, TrajectoryPoint, TrackingArray, TrackingPoint  # 실제 메시지 파일 경로를 맞게 수정
from visualization_msgs.msg import Marker, MarkerArray
import tf
import time
from math import pi

AEB = 100
STOP = 101
SAFE = 102


class Vehicle:
    def __init__(self, position, velocity, acceleration, roll=0.0, pitch=0.0, yaw=0.0, yawrate=0.0):
        self.position = np.array(position)  # [e, n]
        self.velocity = np.array(velocity)  # [vx, vy]
        self.acceleration = np.array(acceleration)
        self.roll = roll
        self.yaw = yaw
        self.pitch = pitch
        self.yawrate = yawrate

class CollisionChecker:
    def __init__(self):
        self.vehicle = []
        self.ego_vehicle = []
        #self.current_road_option = 0
        self.ego_roll = 0.0
        self.ego_pitch = 0.0
        self.ego_yaw = 0.0
        self.min_collision_time = 999
        #self.ego_trajectory = TrajectoryArray()

        rospy.init_node('collision_checker', anonymous=True)

        self.object_info_sub = rospy.Subscriber("/object_tracking", TrackingPoint, self.object_info_callback)
        self.ego_info_sub = rospy.Subscriber("/ego_tracking", TrackingPoint, self.ego_info_callback)

        self.collision_risk_pub = rospy.Publisher("collision_risk", Int32, queue_size=10)
        self.velocity_increase_pub = rospy.Publisher("velocity_increase", Int32, queue_size=10)
       
        # 추가 publisher 선언
        self.min_collision_time_pub = rospy.Publisher("min_collision_time", Float32, queue_size=10)
        self.vehicle_within_range_pub = rospy.Publisher("vehicles_within_range", Int32, queue_size=10)
        self.risk_score_pub = rospy.Publisher("risk_score", Float32, queue_size=10)
        self.distance_to_ego_pub = rospy.Publisher("distance_to_ego",Float32, queue_size=10)
        #self.marker_pub = rospy.Publisher("surround_motion_prediction", MarkerArray, queue_size=10)
        self.trajectory_marker_pub = rospy.Publisher("trajectory_prediction", MarkerArray, queue_size=800)

        #self.predicted_trajectories_pub = rospy.Publisher("predicted_trajectories", TrajectoryArray, queue_size=10)

    # def imu_callback(self, msg):
    #     # Convert quaternion to roll, pitch, yaw
    #     quat = tf.transformations.quaternion_from_euler(msg.orientation.x, msg.orientation.y, msg.orientation.z, msg.orientation.w)
    #     (self.ego_roll, self.ego_pitch, self.ego_yaw) = tf.transformations.euler_from_quaternion(quat)

    #     # Debugging
    #     rospy.loginfo(f"IMU Callback - Roll: {self.ego_roll}, Pitch: {self.ego_pitch}, Yaw: {self.ego_yaw}")

    # def road_option_callback(self, msg):
    #     self.current_road_option = msg.data




    def calculate_risk_score(self, min_collision_time, distance_to_ego):
        # 위험도 점수 계산 로직 (예시)
        time_factor = max(0, 1 - (min_collision_time / 5.0))  # 5초 이내면 위험
        # vehicle_factor = min(1, vehicles_within_range / 10.0)  # 10대 이상이면 최대 위험
        distance_factor = max(0, 1 - (distance_to_ego / 50.0))  # 50m 이내면 위험
        
        risk_score = (time_factor * 0.5 + distance_factor * 0.2) * 100
        return risk_score

    def object_info_callback(self, msg):
        self.vehicle = []  # Clear the previous vehicles list

        vehicle = Vehicle(
            position=[msg.x, msg.y],
            velocity=[msg.vx, msg.vy],
            acceleration=[msg.ax, msg.ay],
            yaw=msg.yaw * np.pi/180, #rad/s
            yawrate = msg.yawrate * np.pi/180 #rad/s
            # roll = 0
            # pitch = 0
        )
        self.vehicle=vehicle

    def ego_info_callback(self, msg):
        self.ego_vehicle = []

        ego_vehicle = Vehicle(
            position = [msg.x, msg.y],
            velocity = [msg.vx, msg.vy],
            acceleration = [msg.ax, msg.ay],
            yaw = msg.yaw * np.pi/180, #rad/s 
            yawrate = msg.yawrate * np.pi/180 #rad/s
        )
        self.ego_vehicle = ego_vehicle  # Update the ego trajectory

    def predict_motion_CV(self, vehicle, time_horizon, dt):
        trajectory_array = TrajectoryArray()
        current_position = vehicle.position

        positions = []  # To store [x, y] pairs

        # Predict position at each time step
        for t in np.arange(0, time_horizon + dt, dt):
            point = TrajectoryPoint()

            # Calculate local deltas
            del_x = vehicle.velocity[0] * t
            del_y = vehicle.velocity[1] * t

            # Update the point position in global coordinates
            point.x = current_position[0] + del_x
            point.y = current_position[1] + del_y

            # Update the yaw (assuming constant yaw rate)
            point.yaw = vehicle.yaw
            point.time = t

            trajectory_array.points.append(point)

        return trajectory_array
    
    def predict_motion_CTRV(self, vehicle, time_horizon, dt):
        trajectory_array = TrajectoryArray()
        current_position = vehicle.position
        yaw = vehicle.yaw  # rad/s
        yawrate = vehicle.yawrate  # rad/s

        positions = []  # To store [x, y] pairs

        # 속도 크기를 계산 (지도 좌표계에서의 속도 크기)
        speed = np.sqrt(vehicle.velocity[0]**2 + vehicle.velocity[1]**2)

        # Predict position at each time step
        for t in np.arange(0, time_horizon + dt, dt):
            point = TrajectoryPoint()

            if abs(yawrate) > 1e-5:  # Yaw rate is not zero (CTRV model)
                # CTRV 모델의 경우 yaw_rate를 고려한 회전 운동
                del_x = (speed / yawrate) * (np.sin(yaw + yawrate * dt) - np.sin(yaw))
                del_y = (speed / yawrate) * (np.cos(yaw) - np.cos(yaw + yawrate * dt))

                # Update yaw for the next timestep
                yaw += yawrate * dt

            else:  # Yaw rate is zero (straight motion)
                del_x = speed * dt * np.cos(yaw)
                del_y = speed * dt * np.sin(yaw)

            # Update the point position in global coordinates
            current_position[0] += del_x
            current_position[1] += del_y
            point.x = current_position[0]
            point.y = current_position[1]


            # Update the yaw (yaw increases linearly with time in CTRV model)
            point.yaw = yaw
            point.time = t

            trajectory_array.points.append(point)

        return trajectory_array
    
    def predict_motion_CTRA(self, vehicle, time_horizon, dt):
        trajectory_array = TrajectoryArray()
        current_position = np.array(vehicle.position) #mp.array 사용으로 초기 값을 쓸 수 있음
        yaw = vehicle.yaw # rad/s
        yawrate = vehicle.yawrate # rad/s

        positions = []  # To store [x, y] pairs

        # 속도 크기를 계산 (지도 좌표계에서의 속도 크기)
        speed = np.sqrt(vehicle.velocity[0]**2 + vehicle.velocity[1]**2)
        vx = vehicle.velocity[0] 
        vy = vehicle.velocity[1]
        ax = vehicle.acceleration[0]
        ay = vehicle.acceleration[1]

        # Predict position at each time step
        for t in np.arange(0, time_horizon + dt, dt):
            point = TrajectoryPoint()

            # 속도를 가속도 값에 따라 업데이트
            vx = vx + ax * dt
            vy = vx + ay * dt

            # 속도 크기를 다시 계산
            speed = np.sqrt(vx**2 + vy**2)

            if abs(yawrate) > 1e-5:  # Yaw rate is not zero (CTRV model)
                # CTRV 모델의 경우 yaw_rate를 고려한 회전 운동
                del_x = (speed / yawrate) * (np.sin(yaw + yawrate * dt) - np.sin(yaw))
                del_y = (speed / yawrate) * (np.cos(yaw) - np.cos(yaw + yawrate * dt))

                # Update yaw for the next timestep
                yaw += yawrate * dt

            else:  # Yaw rate is zero (straight motion)
                del_x = speed * dt * np.cos(yaw)
                del_y = speed * dt * np.sin(yaw)

            # Update the point position in global coordinates
            current_position[0] += del_x
            current_position[1] += del_y
            point.x = current_position[0]
            point.y = current_position[1]

            # Store the updated [x, y] position
            positions.append([point.x, point.y])

            # Update the yaw (yaw increases linearly with time in CTRV model)
            point.yaw = yaw # rad/s
            point.time = t

            trajectory_array.points.append(point)

        return trajectory_array

    def calculate_circles_CTRA(self, position, yaw, vehicle_width, vehicle_length, radius):
        """
        Calculate the positions of 3 circles (front, center, rear) based on vehicle's position and yaw.
        
        Args:
        - position (numpy array of shape (2,)): [x, y] position of the vehicle
        - yaw (float): The yaw angle (in radians)
        - vehicle_width, vehicle_length, radius: Not used in this specific function
        
        Returns:
        - circles (list of numpy arrays): List containing the [x, y] coordinates of the front, center, and rear circles
        """
        circles = []

        # Vehicle's center position
        center = np.array([position[0], position[1]])

        # Define the rotation matrix using yaw (2D rotation)
        rotation_matrix = np.array([[np.cos(yaw), -np.sin(yaw)], 
                                    [np.sin(yaw), np.cos(yaw)]])

        # Define points in the vehicle's local frame
        front_point = np.array([2.84, 0.0])  # x: +2, y: 0
        center_point = np.array([1.34, 0.0])  # x: 0, y: 0
        rear_point = np.array([-0.16, 0.0])  # x: -2, y: 0

        # Rotate the points to the global frame using the yaw value
        front_global = np.dot(rotation_matrix, front_point)
        center_global = np.dot(rotation_matrix, center_point)
        rear_global = np.dot(rotation_matrix, rear_point)

        # Translate the points to the vehicle's global position
        front_circle = center + front_global
        center_circle = center + center_global
        rear_circle = center + rear_global

        # Store the circle positions in the list
        circles.append(front_circle)
        circles.append(center_circle)
        circles.append(rear_circle)

        return circles


    def calculate_circles(self, position, yaw, vehicle_width, vehicle_length, radius):
        """
        Calculate the positions of 3 circles (front, center, rear) using roll, pitch, and yaw (RPY) angles.
        
        Args:
        - position (numpy array of shape (2,)): [x, y] position of the vehicle
        - roll, pitch, yaw (float): The roll, pitch, and yaw angles (in radians)
        - vehicle_width, vehicle_length, radius: Not used in this specific function
        
        Returns:
        - circles (list of numpy arrays): List containing the [x, y] coordinates of the front, center, and rear circles
        """
        circles = []

        # Vehicle's center position
        center = np.array([position[0], position[1]]) # map frame

        # Define the transformation matrix using roll, pitch, yaw (RPY)
        R = np.array([[np.cos(yaw), -np.sin(yaw)], 
                    [np.sin(yaw), np.cos(yaw)]])


        # Define points in the vehicle's local frame (assuming z=0 for a 2D transformation)
        front_point = np.array([2.84, 0.0])  # x: +2, y: 0
        center_point = np.array([1.34, 0.0])  # x: 0, y: 0
        rear_point = np.array([-0.16, 0.0])  # x: -2, y: 0

        # Transform the points to the global frame using the inverse transformation
        front_global = np.dot(R, front_point)
        center_global = np.dot(R, center_point)
        rear_global = np.dot(R, rear_point)

        # Convert the 3D points to 2D by ignoring the z component
        front_circle = center + np.array([front_global[0], front_global[1]])
        center_circle = center + np.array([center_global[0], center_global[1]])
        rear_circle = center + np.array([rear_global[0], rear_global[1]])

        # Store the circle positions in the list
        circles.append(front_circle)
        circles.append(center_circle)
        circles.append(rear_circle)

        return circles

    def visualize_circles(self, circle_positions, vehicle_id,ego_vehicle_radius, surround_vehicle_radius):
        """
        Visualize the circles at each position using MarkerArray.

        Args:
        - circle_positions: List of circle positions (x, y)
        - vehicle_id: Unique ID for the vehicle (0 for ego, 1 for surround)
        """
        marker_array = MarkerArray()

        # vehicle_id에 따른 반지름 설정
        if vehicle_id == 0:  # Ego vehicle
            radius = ego_vehicle_radius
        elif vehicle_id == 1:  # Surround vehicle
            radius = surround_vehicle_radius
        else:
            rospy.logwarn(f"Unknown vehicle_id: {vehicle_id}")
            return

        for i, circle_pos in enumerate(circle_positions):
            circle_marker = Marker()
            circle_marker.header.frame_id = "map"
            circle_marker.header.stamp = rospy.Time.now()
            circle_marker.ns = "vehicle_circles"
            circle_marker.action = Marker.ADD
            circle_marker.pose.orientation.w = 1.0
            circle_marker.lifetime = rospy.Duration(1.0)
            circle_marker.id = vehicle_id * 1000 + i  # Unique ID for each circle
            circle_marker.type = Marker.SPHERE
            
            # Set the scale (diameter is twice the radius)
            circle_marker.scale.x = radius * 2  # Set diameter for x-axis
            circle_marker.scale.y = radius * 2  # Set diameter for y-axis
            circle_marker.scale.z = 0.01  # Flat circles on the ground

            # Set the position of the circle
            circle_marker.pose.position.x = circle_pos[0]
            circle_marker.pose.position.y = circle_pos[1]
            circle_marker.pose.position.z = 0  # Flat on the ground

            # Set the color of the circle
            circle_marker.color.r = 1.0
            circle_marker.color.g = 0.8
            circle_marker.color.b = 0.0
            circle_marker.color.a = 0.5  # Semi-transparent

            marker_array.markers.append(circle_marker)

        # Publish the MarkerArray to RViz
        #self.marker_pub.publish(marker_array)

    def visualize_trajectory(self, trajectory, vehicle_id, ego_radius, surround_radius):
        """
        Visualize the trajectory with circles at each point.
        
        Args:
        - trajectory: The predicted trajectory of the vehicle (TrajectoryArray)
        - vehicle_id: Unique ID for the vehicle (0 for ego, 1 for surround)
        - ego_radius: Radius for ego vehicle circles (default = 1.0)
        - surround_radius: Radius for surround vehicle circles (default = 0.5)
        """
        marker_array = MarkerArray()

        # Line strip for the vehicle's trajectory
        line_strip = Marker()
        line_strip.header.frame_id = "map"
        line_strip.header.stamp = rospy.Time.now()
        line_strip.ns = f"vehicle_{vehicle_id}_trajectory_lines"
        line_strip.action = Marker.ADD
        line_strip.pose.orientation.w = 1.0
        line_strip.lifetime = rospy.Duration(1.0)
        line_strip.id = vehicle_id  # Unique ID for each vehicle trajectory
        line_strip.type = Marker.LINE_STRIP
        line_strip.scale.x = 0.15  # Line thickness

        # Set the line color based on vehicle_id (0 for ego, 1 for surround)
        if vehicle_id == 0:  # Ego vehicle
            line_strip.color.r = 0.0
            line_strip.color.g = 1.0  # Green for ego
            line_strip.color.b = 0.0
        elif vehicle_id == 1:  # Surround vehicle
            line_strip.color.r = 0.0
            line_strip.color.g = 0.0
            line_strip.color.b = 1.0  # Blue for surround
        line_strip.color.a = 0.8  # Higher opacity for better visibility

        # Populate the points in the line strip
        for point in trajectory.points:
            p = Point32()
            p.x = point.x
            p.y = point.y
            p.z = 0  # Flat on the ground
            line_strip.points.append(p)

        marker_array.markers.append(line_strip)

        # Circles (spheres) at each point for the vehicle
        point_id = vehicle_id * 1000  # Start ID for the points, unique per vehicle

        for point in trajectory.points:
            # Get the position of the point
            position = np.array([point.x, point.y])

            # Ego vehicle에서 CTRV와 CTRA를 비교
            # Calculate circles based on vehicle_id and radius
            if vehicle_id == 0:  # Ego vehicle
                circles = self.calculate_circles(position, point.yaw, 1.86, 4.9, ego_radius)
            elif vehicle_id == 1:  # CTRA의 path
                circles = self.calculate_circles_CTRA(position, point.yaw, 1.86, 4.9, ego_radius)

            # For each circle, create a sphere marker
            for i, circle_pos in enumerate(circles):
                circle_marker = Marker()
                circle_marker.header.frame_id = "map"
                circle_marker.header.stamp = rospy.Time.now()
                circle_marker.ns = f"vehicle_{vehicle_id}_trajectory_points_fill"
                circle_marker.action = Marker.ADD
                circle_marker.pose.orientation.w = 1.0
                circle_marker.lifetime = rospy.Duration(1.0)
                circle_marker.id = point_id + i
                circle_marker.type = Marker.SPHERE  # Use SPHERE for the filled circle
                circle_marker.scale.x = 2*ego_radius if vehicle_id == 0 else 2*surround_radius  # Set radius dynamically
                circle_marker.scale.y = 2*ego_radius if vehicle_id == 0 else 2*surround_radius
                circle_marker.scale.z = 0.01  # Flat on the ground

                # Set the circle fill color based on vehicle_id (0 for ego, 1 for surround)
                circle_marker.color = line_strip.color  # Inherit color from line_strip

                # Set the position of the circle
                circle_marker.pose.position.x = circle_pos[0]
                circle_marker.pose.position.y = circle_pos[1]
                circle_marker.pose.position.z = 0

                marker_array.markers.append(circle_marker)

            point_id += 100  # Increment to ensure unique ID for the next point

        # Publish the MarkerArray to RViz
        self.trajectory_marker_pub.publish(marker_array)


    def run(self):
        rate = rospy.Rate(20)  # 20 Hz
        time_horizon = 3.0  # Default time horizon
        dt = 0.2  # 0.2 seconds per step

        # vehicle dimension
        ego_vehicle_width = 1.86
        ego_vehicle_length = 4.9
        ego_vehicle_radius = 1.2 # 0.1 meter for each circle

        # ebt dimension  
        ebt_width = 0.8
        ebt_length = 1.7
        ebt_radius = 1.2

        while not rospy.is_shutdown():
            # Start time measurement
            start_time = time.time()
            collision_detected = False

            min_collision_time = 999  # Initialize to 999 at the start of each cycle
            vehicles_within_range = 0  # Counter for vehicles within 35 meters
            should_increase_velocity = 0  # To determine if ego velocity should be increased for intersection situation
            distance_to_ego = float('inf')  # Initialize distance_to_ego

            #if self.vehicle and self.ego_vehicle:
            if self.ego_vehicle: # Ego vehicle만 관찰
                #vehicle_id = 0  # ID counter for markers

                 # 고정된 vehicle_id 사용
                ego_vehicle_id = 0  # ID for ego vehicle
                surround_vehicle_id = 1  # ID for surround vehicle

                # Get the ego vehicle's yaw angle
                ego_yaw = self.ego_vehicle.yaw # deg/s

                collision_with_behind_vehicle = False

                # Predict Ego vehicle trajectory over the time horizon using the CV model
                rospy.loginfo("!!!!!!!!!! {}".format(self.ego_vehicle.velocity))
                ego_trajectory = self.predict_motion_CTRV(self.ego_vehicle, time_horizon, dt)

                # Calculate the distance between the ego vehicle and the current vehicle
                ego_position = np.array([ego_trajectory.points[0].x, ego_trajectory.points[0].y])
                #vehicle_position = np.array([self.vehicle.position[0], self.vehicle.position[1]])

                # distance_to_ego = np.linalg.norm(ego_position - vehicle_position)

                vehicles_within_range += 1  # Increment the counter for vehicles within range

                # Predict positions over the time horizon using the CV model
                #surround_trajectory = self.predict_motion_CTRV(self.vehicle, time_horizon, dt)
                #surround_trajectory = self.predict_motion_CTRV(self.vehicle, time_horizon, dt)

                # # Ensure surround_trajectory has the same number of points as ego_trajectory
                # if len(surround_trajectory.points) != len(ego_trajectory.points):
                #     rospy.logwarn("Size mismatch between ego and surround trajectory points.")
                #     continue
                
                # CTRA 비교용,
                ego_trajectory_CTRA = self.predict_motion_CTRA(self.ego_vehicle, time_horizon, dt)

                # Visualize both trajectories
                self.visualize_trajectory(ego_trajectory, ego_vehicle_id, ego_vehicle_radius, 0.5*ego_vehicle_radius)  # ego_vehicle_id = 0
                self.visualize_trajectory(ego_trajectory_CTRA, 1, ego_vehicle_radius, 0.5*ego_vehicle_radius)  # surround_vehicle_id = 1


                self.count = 0
            #     for ego_point, surround_point in zip(ego_trajectory.points, surround_trajectory.points):
            #         # Convert points to numpy arrays
            #         ego_position = np.array([ego_point.x, ego_point.y])
            #         surround_position = np.array([surround_point.x, surround_point.y])

            #         # Calculate the positions of the 3 circles for ego and surround vehicles
            #         ego_circles = self.calculate_circles(ego_position, ego_yaw, ego_vehicle_width, ego_vehicle_length, ego_vehicle_radius)
            #         #surround_circles = self.calculate_circles_surround(surround_position, self.vehicle.yaw if self.vehicle.velocity[0] >= 5.0 else ego_yaw, ebt_width, ebt_length, ebt_radius)
            #         surround_circles = self.calculate_circles_surround(surround_position, self.vehicle.yaw, ebt_width, ebt_length, ebt_radius)

            #         if self.count == 0:
            #             self.visualize_circles(ego_circles, ego_vehicle_id,ego_vehicle_radius,ebt_radius)
            #             self.visualize_circles(surround_circles, surround_vehicle_id,ego_vehicle_radius,ebt_radius)

            #         # Check for collision between any of the circles
            #         # collision_detected = False
            #         for ego_circle in ego_circles:
            #             for surround_circle in surround_circles:
            #                 #rospy.loginfo("!!!!!!")
            #                 distance = np.linalg.norm(ego_circle - surround_circle)
            #                 # rospy.loginfo("distance: {}".format(distance))
            #                 if distance < 2.4:
            #                     min_collision_time = min(min_collision_time, ego_point.time)
            #                     collision_detected = True
            #                     break
                        
            #             if collision_detected:
            #                 break
                    
            #         if collision_detected:
            #             break
                    
            #         #rospy.loginfo(collision_detected)
            #         self.count +=1

            #     rospy.loginfo("Min TTC : {} seconds, Check: {}".format(min_collision_time, collision_detected))

            #     # Clear the vehicle list for the next cycle
            #     self.vehicle=[]

            # # Publish Collision risk: TTC threshold 3.0 for stopping and 1.5 for AEB
            # collision_risk = SAFE
                
            # #추가한 부분
            # self.min_collision_time_pub.publish(Float32(min_collision_time))

            # # 위험도 점수 계산 (예시)
            # risk_score = self.calculate_risk_score(min_collision_time, distance_to_ego)
            # risk_score_msg = Float32(data=risk_score)
            # self.risk_score_pub.publish(risk_score_msg)

            # collision_risk_msg = Int32(data=collision_risk)
            # self.collision_risk_pub.publish(collision_risk_msg)

            # # Publish the velocity increase bool
            # velocity_increase_msg = Int32(data=should_increase_velocity)
            # self.velocity_increase_pub.publish(velocity_increase_msg)

            # # 상대 차량과 거리 Publish
            # self.distance_to_ego_pub.publish(distance_to_ego)

            # # End time measurement
            # elapsed = time.time() - start_time
            # #rospy.loginfo(f"Loop execution time: {elapsed} seconds")
            # #rospy.loginfo(f"Ego position: x = {ego_trajectory.points[0].x}, y = {ego_trajectory.points[0].y}, yaw = {ego_trajectory.points[0].yaw}")

            rate.sleep()

if __name__ == "__main__":
    # rospy.init_node('collision_risk_node')
    collision_checker = CollisionChecker()  # CollisionChecker 클래스의 인스턴스를 생성

    try:
        collision_checker.run()  # 인스턴스의 run() 메서드 호출
    except rospy.ROSInterruptException:
        pass