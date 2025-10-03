"""
Celery tasks for video processing and HLS conversion
Requirements:
- pip install celery ffmpeg-python pillow
- FFmpeg must be installed on the system
"""

from celery import shared_task, current_task
from django.conf import settings
from django.utils import timezone
from django.core.files.base import ContentFile
import ffmpeg
import json
import os
import shutil
import logging
from pathlib import Path
from PIL import Image
import subprocess

logger = logging.getLogger(__name__)


class VideoProcessor:
    """Handles video processing and HLS conversion"""

    # Quality presets (width, height, video_bitrate, audio_bitrate)
    QUALITY_PRESETS = {
        'low': {
            'width': 640,
            'height': 360,
            'video_bitrate': '800k',
            'audio_bitrate': '96k',
            'label': '360p'
        },
        'medium': {
            'width': 1280,
            'height': 720,
            'video_bitrate': '2800k',
            'audio_bitrate': '128k',
            'label': '720p'
        },
        'high': {
            'width': 1920,
            'height': 1080,
            'video_bitrate': '5000k',
            'audio_bitrate': '192k',
            'label': '1080p'
        }
    }

    SEGMENT_DURATION = 4  # seconds

    def __init__(self, video_instance):
        self.video = video_instance
        self.video_id = str(video_instance.id)
        self.base_path = Path(settings.MEDIA_ROOT) / 'videos' / 'processed' / self.video_id
        self.original_path = Path(settings.MEDIA_ROOT) / str(video_instance.original_file)

    def process(self):
        """Main processing pipeline"""
        try:
            logger.info(f"Starting video processing for {self.video_id}")

            # Step 1: Create directory structure
            self._create_directory_structure()
            self._update_progress(5, "Created directory structure")

            # Step 2: Extract video metadata
            metadata = self._extract_metadata()
            self._save_metadata(metadata)
            self._update_video_metadata(metadata)
            self._update_progress(10, "Extracted metadata")

            # Step 3: Generate thumbnail
            self._generate_thumbnail()
            self._update_progress(15, "Generated thumbnail")

            # Step 4: Process each quality
            qualities_info = []
            progress_per_quality = 75 / len(self.QUALITY_PRESETS)  # 75% for all qualities

            for idx, (quality_name, preset) in enumerate(self.QUALITY_PRESETS.items()):
                logger.info(f"Processing {quality_name} quality...")

                quality_info = self._process_quality(quality_name, preset, metadata)
                qualities_info.append(quality_info)

                progress = 15 + ((idx + 1) * progress_per_quality)
                self._update_progress(int(progress), f"Processed {quality_name} quality")

            # Step 5: Generate master playlist
            self._generate_master_playlist(qualities_info)
            self._update_progress(95, "Generated master playlist")

            # Step 6: Save quality records to database
            self._save_quality_records(qualities_info)
            self._update_progress(98, "Saved quality records")

            # Step 7: Finalize
            self._finalize_processing()
            self._update_progress(100, "Completed")

            logger.info(f"Video processing completed for {self.video_id}")
            return True

        except Exception as e:
            logger.error(f"Error processing video {self.video_id}: {str(e)}")
            self._handle_error(str(e))
            raise

    def _create_directory_structure(self):
        """Create directory structure for processed video"""
        self.base_path.mkdir(parents=True, exist_ok=True)

        for quality in self.QUALITY_PRESETS.keys():
            quality_path = self.base_path / quality
            quality_path.mkdir(exist_ok=True)

    def _extract_metadata(self):
        """Extract video metadata using ffprobe"""
        try:
            probe = ffmpeg.probe(str(self.original_path))
            video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
            audio_info = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)

            # Calculate duration
            duration = float(probe['format'].get('duration', 0))

            metadata = {
                'duration': duration,
                'width': int(video_info['width']),
                'height': int(video_info['height']),
                'fps': eval(video_info.get('r_frame_rate', '0/1')),
                'codec': video_info.get('codec_name', ''),
                'bitrate': int(probe['format'].get('bit_rate', 0)) // 1000,  # Convert to kbps
                'size': int(probe['format'].get('size', 0)),
                'has_audio': audio_info is not None,
                'audio_codec': audio_info.get('codec_name', '') if audio_info else '',
            }

            return metadata

        except Exception as e:
            logger.error(f"Error extracting metadata: {str(e)}")
            raise

    def _save_metadata(self, metadata):
        """Save metadata to JSON file"""
        metadata_path = self.base_path / 'metadata.json'

        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)

        # Update video model
        relative_path = os.path.relpath(metadata_path, settings.MEDIA_ROOT)
        self.video.metadata_file_path = relative_path
        self.video.save(update_fields=['metadata_file_path'])

    def _update_video_metadata(self, metadata):
        """Update video model with extracted metadata"""
        self.video.duration_seconds = metadata['duration']
        self.video.width = metadata['width']
        self.video.height = metadata['height']
        self.video.fps = metadata['fps']
        self.video.codec = metadata['codec']
        self.video.bitrate = metadata['bitrate']
        self.video.original_file_size = metadata['size']
        self.video.save(update_fields=[
            'duration_seconds', 'width', 'height', 'fps',
            'codec', 'bitrate', 'original_file_size'
        ])

    def _generate_thumbnail(self):
        """Generate thumbnail from video at 25% position"""
        try:
            thumbnail_path = self.base_path / 'thumbnail.jpg'

            # Calculate timestamp (25% into video)
            timestamp = self.video.duration_seconds * 0.25 if self.video.duration_seconds else 1

            # Extract frame using ffmpeg
            (
                ffmpeg
                .input(str(self.original_path), ss=timestamp)
                .output(str(thumbnail_path), vframes=1, format='image2', vcodec='mjpeg')
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True, quiet=True)
            )

            # Optionally resize thumbnail
            img = Image.open(thumbnail_path)
            img.thumbnail((640, 360), Image.Resampling.LANCZOS)
            img.save(thumbnail_path, 'JPEG', quality=85)

            # Save to model
            with open(thumbnail_path, 'rb') as f:
                self.video.thumbnail_file.save(
                    f'thumbnail_{self.video_id}.jpg',
                    ContentFile(f.read()),
                    save=True
                )

            logger.info(f"Thumbnail generated for {self.video_id}")

        except Exception as e:
            logger.warning(f"Error generating thumbnail: {str(e)}")
            # Non-critical error, continue processing

    def _process_quality(self, quality_name, preset, metadata):
        """Process a single quality variant"""
        quality_path = self.base_path / quality_name
        playlist_path = quality_path / 'playlist.m3u8'
        segment_pattern = quality_path / 'segment_%04d.ts'

        # Determine output resolution (maintain aspect ratio)
        input_width = metadata['width']
        input_height = metadata['height']
        target_width = preset['width']
        target_height = preset['height']

        # Calculate scaled dimensions maintaining aspect ratio
        aspect_ratio = input_width / input_height
        target_aspect = target_width / target_height

        if aspect_ratio > target_aspect:
            # Video is wider, scale by width
            scale_width = target_width
            scale_height = int(target_width / aspect_ratio)
            # Make height even
            scale_height = scale_height - (scale_height % 2)
        else:
            # Video is taller, scale by height
            scale_height = target_height
            scale_width = int(target_height * aspect_ratio)
            # Make width even
            scale_width = scale_width - (scale_width % 2)

        try:
            # Build ffmpeg command for HLS conversion
            input_stream = ffmpeg.input(str(self.original_path))

            # Video processing
            video = (
                input_stream.video
                .filter('scale', scale_width, scale_height)
                .filter('format', 'yuv420p')  # Ensure compatibility
            )

            # Audio processing
            audio = input_stream.audio

            # Output with HLS settings
            output_args = {
                'format': 'hls',
                'start_number': 0,
                'hls_time': self.SEGMENT_DURATION,
                'hls_list_size': 0,
                'hls_segment_filename': str(segment_pattern),
                'c:v': 'libx264',
                'b:v': preset['video_bitrate'],
                'maxrate': preset['video_bitrate'],
                'bufsize': str(int(preset['video_bitrate'].rstrip('k')) * 2) + 'k',
                'preset': 'medium',
                'g': int(metadata['fps'] * self.SEGMENT_DURATION),  # Keyframe interval
                'sc_threshold': 0,
                'c:a': 'aac',
                'b:a': preset['audio_bitrate'],
                'ac': 2,
            }

            output = ffmpeg.output(
                video, audio,
                str(playlist_path),
                **output_args
            )

            # Run ffmpeg
            ffmpeg.run(output, overwrite_output=True, capture_stdout=True, capture_stderr=True)

            # Count segments and calculate total size
            segment_files = list(quality_path.glob('segment_*.ts'))
            total_size = sum(f.stat().st_size for f in segment_files)

            quality_info = {
                'quality': quality_name,
                'resolution_width': scale_width,
                'resolution_height': scale_height,
                'bitrate': int(preset['video_bitrate'].rstrip('k')),
                'playlist_path': os.path.relpath(playlist_path, settings.MEDIA_ROOT),
                'segment_count': len(segment_files),
                'total_size': total_size,
                'label': preset['label']
            }

            logger.info(f"Processed {quality_name}: {len(segment_files)} segments, {total_size / (1024 * 1024):.2f} MB")

            return quality_info

        except ffmpeg.Error as e:
            logger.error(f"FFmpeg error processing {quality_name}: {e.stderr.decode()}")
            raise

    def _generate_master_playlist(self, qualities_info):
        """Generate HLS master playlist"""
        master_path = self.base_path / 'master.m3u8'

        with open(master_path, 'w') as f:
            f.write('#EXTM3U\n')
            f.write('#EXT-X-VERSION:3\n\n')

            for quality in qualities_info:
                bandwidth = quality['bitrate'] * 1000  # Convert to bps
                resolution = f"{quality['resolution_width']}x{quality['resolution_height']}"

                f.write(f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},'
                        f'RESOLUTION={resolution},'
                        f'NAME="{quality["label"]}"\n')
                f.write(f'{quality["quality"]}/playlist.m3u8\n')

        # Update video model
        relative_path = os.path.relpath(master_path, settings.MEDIA_ROOT)
        self.video.master_playlist_path = relative_path
        self.video.save(update_fields=['master_playlist_path'])

        logger.info(f"Master playlist generated: {master_path}")

    def _save_quality_records(self, qualities_info):
        """Save VideoQuality records to database"""
        from .models import VideoQuality

        for quality_info in qualities_info:
            VideoQuality.objects.update_or_create(
                video=self.video,
                quality=quality_info['quality'],
                defaults={
                    'resolution_width': quality_info['resolution_width'],
                    'resolution_height': quality_info['resolution_height'],
                    'bitrate': quality_info['bitrate'],
                    'playlist_file_path': quality_info['playlist_path'],
                    'segment_duration': self.SEGMENT_DURATION,
                    'total_segments': quality_info['segment_count'],
                    'total_size_bytes': quality_info['total_size'],
                    'is_processed': True,
                }
            )

        logger.info(f"Saved {len(qualities_info)} quality records")

    def _finalize_processing(self):
        """Mark video as ready"""
        self.video.status = 'ready'
        self.video.processing_completed_at = timezone.now()
        self.video.processing_progress = 100
        self.video.is_active = True
        self.video.published_at = timezone.now()
        self.video.save(update_fields=[
            'status', 'processing_completed_at', 'processing_progress',
            'is_active', 'published_at'
        ])

    def _update_progress(self, percentage, step):
        """Update processing progress"""
        self.video.processing_progress = percentage
        self.video.save(update_fields=['processing_progress'])

        # Update celery task state
        if current_task:
            current_task.update_state(
                state='PROGRESS',
                meta={'current': percentage, 'total': 100, 'step': step}
            )

        logger.info(f"Progress: {percentage}% - {step}")

    def _handle_error(self, error_message):
        """Handle processing errors"""
        self.video.status = 'failed'
        self.video.processing_error = error_message
        self.video.processing_completed_at = timezone.now()
        self.video.save(update_fields=[
            'status', 'processing_error', 'processing_completed_at'
        ])

        # Clean up partial files
        if self.base_path.exists():
            try:
                shutil.rmtree(self.base_path)
                logger.info(f"Cleaned up partial files for {self.video_id}")
            except Exception as e:
                logger.error(f"Error cleaning up files: {str(e)}")


@shared_task(bind=True, max_retries=3)
def process_video_task(self, video_id):
    """
    Celery task to process video and generate HLS streams

    Args:
        video_id: UUID of the video to process
    """
    from .models import Video, VideoProcessingQueue

    try:
        # Get video instance
        video = Video.objects.get(id=video_id)

        # Update status
        video.status = 'processing'
        video.processing_started_at = timezone.now()
        video.save(update_fields=['status', 'processing_started_at'])

        # Update queue status
        queue_task = VideoProcessingQueue.objects.filter(
            video=video,
            status='queued'
        ).first()

        if queue_task:
            queue_task.status = 'processing'
            queue_task.task_id = self.request.id
            queue_task.save(update_fields=['status', 'task_id'])

        # Process video
        processor = VideoProcessor(video)
        processor.process()

        # Update queue on success
        if queue_task:
            queue_task.status = 'completed'
            queue_task.progress_percentage = 100
            queue_task.save(update_fields=['status', 'progress_percentage'])

        logger.info(f"Successfully processed video {video_id}")
        return {'status': 'success', 'video_id': str(video_id)}

    except Video.DoesNotExist:
        logger.error(f"Video {video_id} not found")
        raise

    except Exception as e:
        logger.error(f"Error in process_video_task: {str(e)}")

        # Update queue on failure
        if queue_task:
            queue_task.status = 'failed'
            queue_task.error_message = str(e)
            queue_task.retry_count += 1
            queue_task.save(update_fields=['status', 'error_message', 'retry_count'])

        # Retry logic
        if queue_task and queue_task.retry_count < queue_task.max_retries:
            # Retry after delay (exponential backoff)
            retry_delay = 60 * (2 ** queue_task.retry_count)  # 60s, 120s, 240s
            raise self.retry(exc=e, countdown=retry_delay)

        raise


@shared_task
def cleanup_old_processing_tasks():
    """
    Clean up stale processing tasks
    Run periodically (e.g., every hour)
    """
    from .models import VideoProcessingQueue, Video
    from datetime import timedelta

    # Find tasks stuck in processing for more than 2 hours
    stale_threshold = timezone.now() - timedelta(hours=2)

    stale_tasks = VideoProcessingQueue.objects.filter(
        status='processing',
        started_at__lt=stale_threshold
    )

    for task in stale_tasks:
        logger.warning(f"Cleaning up stale task for video {task.video.id}")

        task.status = 'failed'
        task.error_message = 'Task timed out or was interrupted'
        task.save()

        task.video.status = 'failed'
        task.video.processing_error = 'Processing timed out'
        task.video.save()

    logger.info(f"Cleaned up {stale_tasks.count()} stale tasks")


@shared_task
def cleanup_failed_uploads_task():
    """Celery task wrapper for cleanup function"""
    from .views import cleanup_failed_uploads
    return cleanup_failed_uploads()

@shared_task
def process_queued_videos():
    """
    Process videos in the queue
    Run periodically (e.g., every 5 minutes)
    """
    from .models import VideoProcessingQueue

    # Get next queued task with highest priority
    queued_task = VideoProcessingQueue.objects.filter(
        status='queued'
    ).order_by('-priority', 'queued_at').first()

    if queued_task:
        logger.info(f"Processing queued video: {queued_task.video.id}")
        process_video_task.delay(str(queued_task.video.id))
    else:
        logger.debug("No videos in queue to process")